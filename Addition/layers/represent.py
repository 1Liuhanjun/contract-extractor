"""第0层 文档表征：调用 OCR 得到 markdown+表格，并按 PDF 的 sha1 缓存。

缓存命中则跳过 OCR（也用于 §6 去重的 hash 来源）。
"""
import json
import hashlib
import logging
from pathlib import Path
from providers.ocr import OcrDoc, OcrTable

log = logging.getLogger("represent")


def file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def represent(pdf_path: str, ocr_client, cache_dir: Path, sha1: str = None, use_cache=True,
              on_progress=None) -> OcrDoc:
    if sha1 is None:
        sha1 = file_sha1(pdf_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "ocr.json"

    if use_cache and cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        # 关键：校验缓存的 sha1 与当前文件一致，防同名不同内容 PDF 串档（数据污染）
        if data.get("sha1") == sha1:
            log.info("OCR 缓存命中(sha1 校验通过): %s", cache_file)
            return OcrDoc(
                full_markdown=data.get("full_markdown", ""),
                pages=data.get("pages", []),
                tables=[OcrTable(**t) for t in data.get("tables", [])],
            )
        log.warning("OCR 缓存 sha1 不匹配(缓存=%s 当前=%s)，忽略旧缓存并重新 OCR。",
                    str(data.get("sha1"))[:8], sha1[:8])

    log.info("调用 PaddleOCR-VL 解析 PDF …")
    doc = ocr_client.parse_pdf(pdf_path, on_progress=on_progress)

    # 落缓存（写入 sha1 作为校验依据）
    cache_file.write_text(json.dumps({
        "sha1": sha1,
        "full_markdown": doc.full_markdown,
        "pages": doc.pages,
        "tables": [t.__dict__ for t in doc.tables],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    # 原始返回单独存，便于调试
    (cache_dir / "ocr_raw.json").write_text(
        json.dumps(doc.raw, ensure_ascii=False, indent=1)[:5_000_000], encoding="utf-8")
    log.info("OCR 完成：%d 页，%d 个表格", doc.page_count, len(doc.tables))
    return doc
