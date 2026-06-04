"""联调诊断脚本：分阶段跑通 OCR + 完整 pipeline，把结果摘要与任何报错的
完整 traceback 都写入固定日志文件 output/diag.log，方便贴回/被读取排查。

用法（关掉 VPN 后运行）：
    cd "E:\\实习工作\\博华物流\\合同读取agent\\方案2 ocr\\代码"
    python diag.py                         # 默认跑浙江样例
    python diag.py ..\\..\\材料\\邮政合同\\福建.pdf   # 指定 PDF

日志固定写到： <代码目录>\\output\\diag.log
"""
import sys
import time
import logging
import traceback
from pathlib import Path

# 控制台中文容错（Windows GBK 控制台避免 UnicodeEncodeError 中断）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR))

from app_config import Config
from providers.ocr import build_ocr
from providers.llm import build_llm
from layers import excel_writer as L5
from layers import represent as L0
import pipeline

DEFAULT_PDF = CODE_DIR / "samples" / "浙江.pdf"


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_PDF)
    cfg = Config()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    logfile = cfg.output_dir / "diag.log"

    # 日志：文件(utf-8) + 控制台；级别 INFO（含 OCR 轮询进度、各层日志）
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    fh = logging.FileHandler(logfile, encoding="utf-8", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    log = logging.getLogger("diag")

    log.info("==================== 联调开始 ====================")
    log.info("PDF = %s", pdf)
    if not Path(pdf).exists():
        log.error("文件不存在：%s", pdf)
        return

    # —— 阶段1：仅 OCR（验证连通 + 协议）——
    # 走 represent 缓存（与 pipeline 同一缓存键），阶段2 即可命中、不重复 OCR
    try:
        ocr = build_ocr(cfg)
        log.info("[OCR] job_url=%s  model=%s  token设置=%s",
                 ocr.job_url, ocr.model, bool(ocr.token))
        t = time.time()
        sha1 = L0.file_sha1(pdf)
        cache_dir = cfg.output_dir / "_cache" / f"{Path(pdf).stem}_{sha1[:10]}"
        doc = L0.represent(pdf, ocr, cache_dir, sha1=sha1)
        log.info("[OCR] ✅ 成功：用时 %.0fs，页数=%d，表格数=%d",
                 time.time() - t, doc.page_count, len(doc.tables))
        log.info("[OCR] 第1页 markdown 前 400 字：\n%s", (doc.pages[0] if doc.pages else "")[:400])
        if doc.tables:
            log.info("[OCR] 表格[0] HTML 前 600 字：\n%s", doc.tables[0].html[:600])
    except Exception:
        log.error("[OCR] ❌ 失败，完整 traceback：\n%s", traceback.format_exc())
        log.info("（OCR 这步若是 Read timed out，多半仍是 VPN/网络；其它报错把本段贴回即可）")
        log.info("==================== 结束（OCR 失败）日志：%s ====================", logfile)
        return

    # —— 阶段2：完整 pipeline（分类→抽取→换算→写表→报告）——
    try:
        out_xlsx = cfg.output_dir / "contracts" / f"合同台账_{pdf.stem}.xlsx"
        index = L5.load_index(cfg.output_dir)
        llm = build_llm(cfg)
        log.info("[LLM] base_url=%s model=%s key设置=%s",
                 cfg.llm.get("base_url"), cfg.llm.get("model"), bool(cfg.llm_key))
        r = pipeline.process_pdf(pdf, cfg, llm, ocr, out_xlsx, index)
        log.info("[PIPELINE] ✅ 结果：%s", r)
        log.info("[PIPELINE] 本合同台账：%s", out_xlsx)
        if r.get("report"):
            log.info("[PIPELINE] 复核报告：%s", r["report"])
    except Exception:
        log.error("[PIPELINE] ❌ 失败，完整 traceback：\n%s", traceback.format_exc())

    log.info("==================== 联调结束。完整日志：%s ====================", logfile)


if __name__ == "__main__":
    main()
