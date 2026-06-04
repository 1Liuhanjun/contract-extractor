"""主编排：单份 PDF 的完整处理流水线（第0~6层）。"""
import time
import shutil
import logging
import contextlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from schemas import ContractResult
from layers import represent as L0
from layers import classify as L1
from layers import extract as L2
from layers import calculate as L3
from layers import validate as L4
from layers import excel_writer as L5
from layers import report as L6
from layers import price_ocr

log = logging.getLogger("pipeline")


def process_pdf(pdf_path: str, cfg, llm, ocr, out_xlsx: Path, index: dict,
                progress=None, index_lock=None) -> dict:
    """处理单份 PDF。

    progress: 可选回调 Callable[[dict], None]，在每层边界被调用，用于前端实时进度。
              不传则行为与原 CLI 完全一致（emit 为空操作）。
    index_lock: 多份合同并发处理时传入的锁，保护共享 index 的读写 / _index.json 落盘；
                单线程（CLI）不传 → 空上下文，无开销。
    """
    ilock = index_lock or contextlib.nullcontext()

    def emit(**ev):
        if progress:
            try:
                progress(ev)
            except Exception:  # noqa  进度推送失败绝不影响主流程
                pass

    pdf_path = str(pdf_path)
    contract_id = Path(pdf_path).stem
    t0 = time.time()
    output_dir = cfg.output_dir
    emit(stage="queued", status="done", contract_id=contract_id)

    # 缓存键：缓存目录并入 sha1，防同名不同内容 PDF 串档（数据污染）。
    # 不再因「该 sha1 处理过」而跳过——允许用户任意重传，每次都独立重新抽取、重新写表（追加新行）。
    # 数据隔离保证：OCR 缓存目录按「文件名 + 内容哈希」命名，且 represent() 内部还二次校验 sha1
    # 一致才命中；只有文件内容完全相同才会复用它自身的 OCR（属正常省钱，非跨文件污染）。
    # 内容只要有一点不同 sha1 即不同 → 走全新缓存目录，因此二次上传绝不会被首次/其它文件的结果影响。
    sha1 = L0.file_sha1(pdf_path)
    cache_dir = output_dir / "_cache" / f"{contract_id}_{sha1[:10]}"
    with ilock:
        reprocessed = L5.is_duplicate(index, sha1)  # 此前处理过 → 本次为重传重跑（仅提示，不跳过）
        prev_status = index[sha1].get("status") if reprocessed else None
    if reprocessed:
        log.info("该 PDF（sha1=%s）此前已处理过(status=%s)，现按重传重新处理。", sha1[:8], prev_status)

    # 第0层 OCR（异步任务，轮询时把逐页进度透传给前端，避免长时间无反馈）
    emit(stage="ocr", status="start")

    def _ocr_progress(extracted, total, state):
        emit(stage="ocr", status="progress", extracted=extracted, total=total, ocr_state=state)

    doc = L0.represent(pdf_path, ocr, cache_dir, sha1=sha1, on_progress=_ocr_progress)
    emit(stage="ocr", status="done", page_count=doc.page_count)
    llm_calls_start = getattr(llm, "call_count", 0)
    tokens_start = getattr(llm, "total_tokens", 0)

    # 第1层 分类
    emit(stage="classify", status="start")
    company, conf = L1.classify_company(doc.full_markdown, cfg)
    emit(stage="classify", status="done", company=company, confidence=conf)
    # 一期仅处理 supported_companies（默认["邮政"]）。TODO(二期)：见 config.yaml 注册表说明。
    if company not in cfg.supported_companies:
        # 未知 → 拷到 _unknown 待人工确认；其它已知公司 → 一期不处理
        if company == "未知":
            unk = output_dir / "_unknown"
            unk.mkdir(parents=True, exist_ok=True)
            shutil.copy(pdf_path, unk / Path(pdf_path).name)
        log.warning("公司分类=%s（置信 %s），一期仅处理邮政，跳过写表。", company, conf)
        emit(stage="skipped", status="done", reason="unsupported_company",
             company=company, confidence=conf)
        return {"status": f"skipped_{company}", "contract_id": contract_id,
                "company": company, "confidence": conf}

    # 第2层 抽取（含 §13.1 证据回查 + self-consistency 投票）
    enhance = cfg.enhance
    result = ContractResult(company=company, company_confidence=conf)
    tables_md = L2.tables_to_md(doc)
    # 速度优化(P1-1)：台账/线路/系数三块互不依赖，并行抽取。
    # 临界路径 = 最慢一块，而非三者之和；不改 extract.py 内部，只改调用编排。
    emit(stage="extract", status="start", sub="ledger")
    emit(stage="extract", status="start", sub="routes")
    emit(stage="extract", status="start", sub="coeff")
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_ledger = ex.submit(L2.extract_ledger,
                               llm, cfg.skill_text, cfg.field_map, doc.full_markdown, enhance)
        fut_routes = ex.submit(L2.extract_routes,
                               llm, cfg.skill_text, cfg.field_map, doc.full_markdown, tables_md, enhance)
        fut_coeff = ex.submit(L2.extract_coefficients,
                              llm, cfg.skill_text, doc.full_markdown, tables_md)
        result.ledger_fields = fut_ledger.result()
        # 注：线路「类型」列已改红字不抽取，不再据计费方式派生；_billing 恒为 None，保留占位以兼容签名。
        result.routes, result.routes_found, _billing = fut_routes.result()
        result.coefficient = fut_coeff.result()
    # 客户分类（台账C列）：恒等于已识别公司，由分类结果**注入**而非固定默认值
    #   （field_map 标 no_llm，不问 LLM；写表读 ledger_fields["客户分类"]）。
    from schemas import Field
    result.ledger_fields["客户分类"] = Field(
        value=company, confidence=conf, evidence_ok=True,
        evidence=f"按公司分类结果填写（分类={company}，置信{conf}）")
    emit(stage="extract", status="done",
         routes=len(result.routes), routes_found=result.routes_found)

    # 第3层 计算
    emit(stage="calculate", status="start")
    calc_notes = L3.compute(result, cfg.field_map)
    emit(stage="calculate", status="done")

    # §13.2 价格数字交叉复核（可选，RapidOCR）：对 LLM 直读价做第二引擎佐证
    price_warnings = price_ocr.cross_check(pdf_path, result, doc, enhance)

    # 第4层 校验
    emit(stage="validate", status="start")
    price_range = tuple(enhance.get("price_range", (1, 1_000_000)))
    warnings = price_warnings + L4.validate(result, cfg.field_map, price_range=price_range)

    # 比照/延申线路机制：价格表只直读部分线路，发往比照地的延申线路需人工派生（系统不臆造）
    ext = L4.check_extension_routes(doc.full_markdown, len(result.routes))
    if ext:
        warnings.insert(0, ext)

    # 合同编号已改红字不抽取 → 不再做「同合同不同文件」软去重告警；
    # 文件级去重仍由内容 sha1 保证（见 _index.json / is_duplicate）。contract_no 留 None 仅作 index 占位。
    contract_no = None
    emit(stage="validate", status="done", warnings=warnings)

    # 第5层 写 Excel —— 每份合同写成独立文件（从模板新建并填充，不与其它合同共用）
    emit(stage="write", status="start")
    with ilock:
        index[sha1] = {"contract_id": contract_id, "contract_no": contract_no, "status": "processing"}
        L5.save_index(output_dir, index)
    out_xlsx = Path(out_xlsx)
    n_rows = L5.write_contract(cfg.template_xlsx, out_xlsx, result.ledger_fields,
                              result.routes, cfg.field_map, cfg.defaults)
    emit(stage="write", status="done", rows=n_rows, xlsx=out_xlsx.name)

    # 第6层 报告
    emit(stage="report", status="start")
    elapsed = time.time() - t0
    llm_calls = getattr(llm, "call_count", 0) - llm_calls_start
    tokens_used = getattr(llm, "total_tokens", 0) - tokens_start
    log.info("本份 %s LLM 用量：调用 %d 次，token 合计 %d（累计 %d）",
             contract_id, llm_calls, tokens_used, getattr(llm, "total_tokens", 0))
    report_path = output_dir / "reviews" / f"{contract_id}_review.md"
    L6.generate(report_path,
                contract_id=contract_id, pdf_path=pdf_path, page_count=doc.page_count,
                company=company, company_conf=conf,
                ledger_fields=result.ledger_fields, field_map=cfg.field_map,
                routes=result.routes, routes_found=result.routes_found,
                calc_notes=calc_notes, warnings=warnings, full_md=doc.full_markdown,
                llm_calls=llm_calls, tokens_used=tokens_used, elapsed_sec=elapsed,
                cache_dir=cache_dir)

    # 登记：标记完成（含本合同独立台账文件名，供历史列表下载）
    with ilock:
        index[sha1] = {"contract_id": contract_id, "contract_no": contract_no,
                       "rows": n_rows, "status": "done", "xlsx": out_xlsx.name}
        L5.save_index(output_dir, index)
    emit(stage="report", status="done", report=str(report_path))

    log.info("完成 %s：邮政写入 %d 行，用时 %.0fs，报告 %s",
             contract_id, n_rows, elapsed, report_path)
    return {"status": "ok", "contract_id": contract_id, "rows": n_rows,
            "report": str(report_path), "report_name": report_path.name,
            "contract_no": contract_no, "warnings": warnings, "reprocessed": reprocessed,
            "xlsx": str(out_xlsx), "xlsx_name": out_xlsx.name,
            "company": company, "confidence": conf, "elapsed_sec": round(elapsed, 1)}
