"""Run the original Addition pipeline from a shared OCR markdown file.

This adapter skips only Addition's OCR submit/poll/download layer. Everything
after OCR still uses the original Addition modules: classify, extract,
calculate, validate, excel_writer, and report.
"""

import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR))

from app_config import Config  # noqa: E402
from providers.llm import build_llm  # noqa: E402
from providers.ocr import OcrDoc, OcrTable  # noqa: E402
from schemas import ContractResult, Field  # noqa: E402
from layers import classify as L1  # noqa: E402
from layers import extract as L2  # noqa: E402
from layers import calculate as L3  # noqa: E402
from layers import validate as L4  # noqa: E402
from layers import excel_writer as L5  # noqa: E402
from layers import report as L6  # noqa: E402
from layers import price_ocr  # noqa: E402
from layers import represent as L0  # noqa: E402


TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
PAGE_MARK_RE = re.compile(r"(?=<!--\s*第\s*\d+\s*页\s*-->)")


def build_doc(markdown_path: Path) -> OcrDoc:
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    chunks = [part.strip() for part in PAGE_MARK_RE.split(text) if part.strip()]
    if not chunks:
        chunks = [text]

    doc = OcrDoc(full_markdown=text, pages=chunks, tables=[], raw={"shared_markdown": str(markdown_path)})
    for page_no, page_text in enumerate(chunks, 1):
        for html in TABLE_RE.findall(page_text):
            doc.tables.append(OcrTable(page=page_no, html=html))
    return doc


def process_shared_ocr(source_path: Path, markdown_path: Path, out_xlsx: Path, cfg: Config) -> dict:
    t0 = time.time()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)

    doc = build_doc(markdown_path)
    llm = build_llm(cfg)
    index = L5.load_index(cfg.output_dir)
    sha1 = L0.file_sha1(str(source_path)) if source_path.exists() else markdown_path.name
    contract_id = source_path.stem

    company, conf = L1.classify_company(doc.full_markdown, cfg)
    if company not in cfg.supported_companies:
        return {
            "status": f"skipped_{company}",
            "contract_id": contract_id,
            "company": company,
            "confidence": conf,
            "rows": 0,
        }

    enhance = cfg.enhance
    result = ContractResult(company=company, company_confidence=conf)
    tables_md = L2.tables_to_md(doc)

    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_ledger = ex.submit(L2.extract_ledger, llm, cfg.skill_text, cfg.field_map, doc.full_markdown, enhance)
        fut_routes = ex.submit(L2.extract_routes, llm, cfg.skill_text, cfg.field_map, doc.full_markdown, tables_md, enhance)
        fut_coeff = ex.submit(L2.extract_coefficients, llm, cfg.skill_text, doc.full_markdown, tables_md)
        result.ledger_fields = fut_ledger.result()
        result.routes, result.routes_found, _billing = fut_routes.result()
        result.coefficient = fut_coeff.result()

    result.ledger_fields["客户分类"] = Field(
        value=company,
        confidence=conf,
        evidence_ok=True,
        evidence=f"按公司分类结果填写（分类={company}，置信{conf}）",
    )

    calc_notes = L3.compute(result, cfg.field_map)
    price_warnings = []
    if source_path.suffix.lower() == ".pdf":
        price_warnings = price_ocr.cross_check(str(source_path), result, doc, enhance)
    price_range = tuple(enhance.get("price_range", (1, 1_000_000)))
    warnings = price_warnings + L4.validate(result, cfg.field_map, price_range=price_range)
    ext = L4.check_extension_routes(doc.full_markdown, len(result.routes))
    if ext:
        warnings.insert(0, ext)

    index[sha1] = {"contract_id": contract_id, "contract_no": None, "status": "processing"}
    L5.save_index(cfg.output_dir, index)
    n_rows = L5.write_contract(cfg.template_xlsx, out_xlsx, result.ledger_fields, result.routes,
                               cfg.field_map, cfg.defaults)

    elapsed = time.time() - t0
    report_path = cfg.output_dir / "reviews" / f"{contract_id}_review.md"
    L6.generate(
        report_path,
        contract_id=contract_id,
        pdf_path=str(source_path),
        page_count=doc.page_count,
        company=company,
        company_conf=conf,
        ledger_fields=result.ledger_fields,
        field_map=cfg.field_map,
        routes=result.routes,
        routes_found=result.routes_found,
        calc_notes=calc_notes,
        warnings=warnings,
        full_md=doc.full_markdown,
        llm_calls=getattr(llm, "call_count", 0),
        tokens_used=getattr(llm, "total_tokens", 0),
        elapsed_sec=elapsed,
        cache_dir=markdown_path.parent,
    )

    index[sha1] = {
        "contract_id": contract_id,
        "contract_no": None,
        "rows": n_rows,
        "status": "done",
        "xlsx": out_xlsx.name,
    }
    L5.save_index(cfg.output_dir, index)

    return {
        "status": "ok",
        "contract_id": contract_id,
        "rows": n_rows,
        "xlsx": str(out_xlsx),
        "xlsx_name": out_xlsx.name,
        "report": str(report_path),
        "warnings": warnings,
        "company": company,
        "confidence": conf,
        "elapsed_sec": round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Run Addition pipeline from shared OCR markdown")
    parser.add_argument("--source", required=True)
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    cfg = Config(args.config)
    result = process_shared_ocr(Path(args.source), Path(args.markdown), Path(args.output), cfg)
    print("SHARED_OCR_RESULT_JSON=" + json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
