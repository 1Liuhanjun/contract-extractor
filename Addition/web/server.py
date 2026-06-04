"""FastAPI 服务：包裹现有 pipeline，为前端提供 上传 / 实时进度(SSE) / 历史 / 下载 接口。

启动（在「方案2 ocr/代码」目录下）：
    uvicorn web.server:app --port 8000          # 生产（同时托管前端 dist/）
    uvicorn web.server:app --reload --port 8000 # 开发（前端用 vite dev + proxy）

设计要点（本地单人场景）：
- 进程启动时初始化一次 Config / llm / ocr / 主台账，并复用（照搬 run.py 的套路）。
- 每次上传生成一个 job_id，后台线程顺序处理该批 PDF；进度事件写入「事件日志」经 SSE 重放下发
  （每个连接从日志头部重放，对浏览器重连 / StrictMode 双连接幂等、不丢事件）。
- 全局一把锁串行化 pipeline 执行，避免多 job 并发写 _index.json / Excel 造成竞争。
- 不改动任何 layers/providers/schemas 逻辑；pipeline.process_pdf 仅新增可选 progress 回调。
"""
import os
import sys
import json
import time
import uuid
import shutil
import logging
import mimetypes
import threading
import concurrent.futures
from pathlib import Path
from typing import List

# Windows 注册表常把 .js 误关联成 text/plain，导致 StaticFiles 发错 MIME、
# 浏览器按 HTML 规范拒绝执行 module script（前端黑屏）。这里强制纠正。
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# 让本文件能 import 上一级目录（代码根）的后端模块
CODE_DIR = Path(__file__).resolve().parent.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from app_config import Config            # noqa: E402
from providers.llm import build_llm      # noqa: E402
from providers.ocr import build_ocr      # noqa: E402
from layers import excel_writer as L5    # noqa: E402
import pipeline                          # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("web")

# ---- 全局：进程启动初始化一次 ----
cfg = Config()
cfg.output_dir.mkdir(parents=True, exist_ok=True)
cfg.input_dir.mkdir(parents=True, exist_ok=True)
CONTRACTS_DIR = cfg.output_dir / "contracts"   # 每份合同写一份独立台账文件
CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
INDEX = L5.load_index(cfg.output_dir)
# 批量上传时同时并发处理的合同数（环境变量优先，其次 config.yaml，默认 3）
MAX_CONCURRENCY = max(1, int(os.environ.get("PIPELINE_CONCURRENCY")
                             or cfg.raw.get("max_concurrency", 3)))
LLM = build_llm(cfg)
OCR = build_ocr(cfg)
UPLOAD_DIR = cfg.output_dir / "_web_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# job_id -> {"events": [ev,...], "cond": Condition, "files": [name], "done": bool}
# 用「事件日志 + 条件变量」而非 Queue：每个 SSE 连接都从日志头部重放，
# 因此对浏览器自动重连 / React StrictMode 双连接都幂等、不丢事件
# （旧的单 Queue 会被两个消费者瓜分，开头事件被已断开的连接吃掉 → 前端空白）。
JOBS: dict = {}
INDEX_LOCK = threading.Lock()  # 保护并发处理时对共享 INDEX / _index.json 的读写

# 历史记录与 sha1 去重的 _index.json **解耦**：每次提交（即便是同一份合同）都追加一条
# **独立**记录，互不覆盖，前端因此能显示每一次处理。_index.json 仍只用于"疑似重复"软告警。
HISTORY_FILE = cfg.output_dir / "_history.json"
HISTORY_LOCK = threading.Lock()


def _load_history() -> list:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _append_history(rec: dict):
    with HISTORY_LOCK:
        hist = _load_history()
        hist.append(rec)
        HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")


def _seed_history_from_index():
    """首次启动若无 _history.json，用旧 _index.json 生成初始历史，避免丢失既有记录。"""
    if HISTORY_FILE.exists():
        return
    seeded = [{
        "ts": None, "job_id": sha1[:12],
        "contract_id": v.get("contract_id"), "contract_no": v.get("contract_no"),
        "rows": v.get("rows"), "status": v.get("status"),
        "xlsx": v.get("xlsx"), "report": v.get("contract_id"),
    } for sha1, v in (INDEX or {}).items()]
    if seeded:
        HISTORY_FILE.write_text(json.dumps(seeded, ensure_ascii=False, indent=1), encoding="utf-8")


_seed_history_from_index()


def _record_history(job_id: str, pdf: Path, r: dict):
    """把单份处理结果追加为**一条独立**历史记录；并把复核报告复制成带 job_id 的独立副本，
    避免同名合同的报告互相覆盖——每次提交都能下到它自己那次的台账与报告。"""
    cid = r.get("contract_id") or pdf.stem
    report_id = cid  # 默认指向 pipeline 写的 <cid>_review.md
    rep = r.get("report")
    if rep and Path(rep).exists():
        dst = cfg.output_dir / "reviews" / f"{job_id}_{cid}_review.md"
        try:
            shutil.copy(rep, dst)
            report_id = f"{job_id}_{cid}"   # 独立副本（download_report 会拼 _review.md）
        except Exception:  # noqa 复制失败不影响主流程，回退指向原报告
            pass
    _append_history({
        "ts": time.time(), "job_id": job_id,
        "contract_id": cid, "contract_no": r.get("contract_no"),
        "rows": r.get("rows"), "status": r.get("status"),
        "xlsx": r.get("xlsx_name"), "report": report_id,
    })


def _emit(job_id: str, ev: dict):
    """把一条事件追加到该 job 的事件日志并唤醒所有 SSE 连接。"""
    job = JOBS.get(job_id)
    if not job:
        return
    with job["cond"]:
        job["events"].append(ev)
        job["cond"].notify_all()

app = FastAPI(title="合同读取智能体")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _process_one(job_id: str, pdf: Path):
    """处理单份 PDF：进度事件绑定**本份**文件名打标签（并发安全）。"""
    def push(ev: dict):
        ev = dict(ev)
        ev["file"] = pdf.name          # 绑定本份，避免并发时进度串到别的合同
        _emit(job_id, ev)

    _emit(job_id, {"type": "file_start", "file": pdf.name})
    try:
        # 每份合同独立台账：用 job_id + 文件名命名，互不覆盖、互不共用
        out_xlsx = CONTRACTS_DIR / f"合同台账_{job_id}_{pdf.stem}.xlsx"
        r = pipeline.process_pdf(pdf, cfg, LLM, OCR, out_xlsx, INDEX,
                                 progress=push, index_lock=INDEX_LOCK)
    except Exception as e:  # noqa  单份失败不影响整批
        log.exception("处理失败：%s", pdf.name)
        r = {"status": "error", "contract_id": pdf.stem, "error": str(e)}
    _emit(job_id, {"type": "file_done", "file": pdf.name, "result": r})
    _record_history(job_id, pdf, r)   # 每次提交都追加一条独立历史（同份合同不再互相覆盖）
    return r


def _run_job(job_id: str, pdfs: List[Path]):
    """后台线程：**并发**处理本批 PDF（每份各自调 OCR/LLM、各写独立台账、各自实时进度）。

    并发度上限 MAX_CONCURRENCY；进度事件按份打标签写入事件日志，前端按文件名分别渲染。
    """
    try:
        workers = max(1, min(MAX_CONCURRENCY, len(pdfs)))
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="pdf") as ex:
            for pdf in pdfs:
                ex.submit(_process_one, job_id, pdf)
            # with 退出时自动等待全部任务完成（_process_one 已各自吞掉异常）
    finally:
        _emit(job_id, {"type": "job_done"})
        if job_id in JOBS:
            JOBS[job_id]["done"] = True


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)):
    """接收 1~N 个 PDF，落盘后起后台线程处理，返回 job_id。"""
    job_id = uuid.uuid4().hex[:12]
    # 每个 job 的源文件落盘到独立子目录：即使多次上传同名 PDF 也互不覆盖、互不串档，
    # 保证每次上传处理的是它自己的字节（防数据污染）。文件名本身保持原样，
    # 故 contract_id / OCR 缓存键（文件名+内容哈希）不变，内容相同的重传仍能正常复用自身缓存。
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    pdfs: List[Path] = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            continue
        dest = job_dir / Path(f.filename).name
        dest.write_bytes(await f.read())
        pdfs.append(dest)
    if not pdfs:
        raise HTTPException(400, "未收到有效的 PDF 文件")

    JOBS[job_id] = {"events": [], "cond": threading.Condition(),
                    "files": [p.name for p in pdfs], "done": False}
    threading.Thread(target=_run_job, args=(job_id, pdfs), daemon=True).start()
    return {"job_id": job_id, "files": [p.name for p in pdfs]}


@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str):
    """SSE：实时下发该 job 的处理进度，收到 job_done 后结束。"""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在或已过期")
    cond = job["cond"]
    events = job["events"]

    def gen():
        # 起手发一个 hello，便于前端确认连接已建立
        yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id}, ensure_ascii=False)}\n\n"
        idx = 0  # 本连接已下发到的事件位置；从 0 开始 → 总是重放完整历史（重连/双连接幂等）
        while True:
            with cond:
                while idx >= len(events):
                    if not cond.wait(timeout=15):
                        break  # 超时 → 跳出发心跳，避免代理/浏览器把空闲连接断掉
                batch = events[idx:]
                idx = len(events)
            if not batch:
                yield ": keepalive\n\n"   # SSE 注释行，浏览器忽略，仅用于保活
                continue
            for ev in batch:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "job_done":
                    return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.get("/api/history")
async def history():
    """读 _history.json（append-only），返回**每一次**处理记录（同份合同多次提交各占一行）。"""
    hist = _load_history()
    items = [{
        # sha1 字段名沿用前端契约，值用 job_id：仅作列表唯一标识/ key，不再做去重
        "sha1": rec.get("job_id") or "",
        "contract_id": rec.get("contract_id"),
        "contract_no": rec.get("contract_no"),
        "rows": rec.get("rows"),
        "status": rec.get("status"),
        "xlsx": rec.get("xlsx"),
        "report": rec.get("report"),   # 独立报告标识（download_report 拼 _review.md）
    } for rec in hist]
    items.reverse()  # 最新在前
    return {"items": items, "count": len(items)}


@app.get("/api/download/excel/{fname}")
async def download_excel(fname: str):
    """下载某份合同的独立台账文件（按文件名，防路径穿越）。"""
    safe = Path(fname).name
    p = CONTRACTS_DIR / safe
    if not p.exists():
        raise HTTPException(404, "该合同台账不存在")
    return FileResponse(
        p, filename=safe,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/download/report/{contract_id}")
async def download_report(contract_id: str):
    safe = Path(contract_id).name  # 防路径穿越
    p = cfg.output_dir / "reviews" / f"{safe}_review.md"
    if not p.exists():
        raise HTTPException(404, "复核报告不存在")
    return FileResponse(p, filename=p.name, media_type="text/markdown")


# ---- 生产：托管前端构建产物（开发期 dist/ 不存在则跳过，前端走 vite proxy）----
DIST = Path(__file__).resolve().parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")
