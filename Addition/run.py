"""CLI 入口。

  python run.py path/to/合同.pdf
  python run.py path/to/pdf_dir/
  python run.py path/to/合同.pdf -o ./somewhere/主表.xlsx
"""
import sys
import argparse
import logging
from pathlib import Path

from app_config import Config
from providers.llm import build_llm
from providers.ocr import build_ocr
from layers import excel_writer as L5
import pipeline


def setup_logging(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO, format=fmt,
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(log_path, encoding="utf-8")],
    )


def collect_pdfs(path: Path):
    if path.is_dir():
        return sorted(path.glob("*.pdf"))
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    return []


def main():
    ap = argparse.ArgumentParser(description="邮政合同读取智能体")
    ap.add_argument("input", nargs="?", help="PDF 文件或目录（省略则处理 ./input 目录下所有 PDF）")
    ap.add_argument("-o", "--output", help="台账输出目录（每份合同各一个 Excel；默认 ./output/contracts/）")
    ap.add_argument("-c", "--config", help="配置文件路径（默认 config.yaml）")
    args = ap.parse_args()

    cfg = Config(args.config)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.input_dir.mkdir(parents=True, exist_ok=True)

    # 每份合同写成独立台账文件，统一放到输出目录下的 contracts/
    out_dir = Path(args.output).resolve() if args.output else (cfg.output_dir / "contracts")
    out_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(cfg.output_dir / "logs" / "run.log")
    log = logging.getLogger("run")

    # 省略参数 → 默认处理 input 目录
    target = Path(args.input).resolve() if args.input else cfg.input_dir
    pdfs = collect_pdfs(target)
    if not pdfs:
        log.error("未找到 PDF：%s（把待处理 PDF 放进 input 目录，或显式传入路径）", target)
        sys.exit(1)

    if not cfg.ocr.get("base_url"):
        log.error("config.yaml 的 ocr.base_url 为空。请登录 https://aistudio.baidu.com/paddleocr/task "
                  "复制 API_URL 填入后再运行。")
        sys.exit(2)

    index = L5.load_index(cfg.output_dir)

    llm = build_llm(cfg)
    ocr = build_ocr(cfg)

    results = []
    for pdf in pdfs:
        log.info("=== 处理 %s ===", pdf.name)
        # 每份合同单独一份日志 logs/<合同标识>.log（同时仍写总 run.log）
        per_log = logging.FileHandler(cfg.output_dir / "logs" / f"{pdf.stem}.log", encoding="utf-8")
        per_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        logging.getLogger().addHandler(per_log)
        try:
            out_xlsx = out_dir / f"合同台账_{pdf.stem}.xlsx"
            r = pipeline.process_pdf(pdf, cfg, llm, ocr, out_xlsx, index)
        except Exception as e:  # noqa
            log.exception("处理失败：%s", pdf.name)
            r = {"status": "error", "contract_id": pdf.stem, "error": str(e)}
        finally:
            logging.getLogger().removeHandler(per_log)
            per_log.close()
        results.append(r)

    log.info("==== 汇总 ====")
    for r in results:
        log.info("%s: %s  → %s", r.get("contract_id"), r.get("status"), r.get("xlsx") or "(未生成台账)")
    log.info("台账输出目录：%s", out_dir)


if __name__ == "__main__":
    main()
