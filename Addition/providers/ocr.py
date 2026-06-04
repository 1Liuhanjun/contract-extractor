"""OCR provider：百度 AI Studio「应用部署」PaddleOCR-VL 异步文档解析 API。

协议（依官方「API 调用示例」）：
1. 提交任务：POST {job_url}
   - 认证 Header：`Authorization: bearer <ACCESS_TOKEN>`（注意小写 bearer）
   - 本地文件走 multipart/form-data：
       data  = {"model": "<MODEL>", "optionalPayload": <json字符串>}
       files = {"file": <文件句柄>}
   - 返回 200，job_id = resp.json()["data"]["jobId"]
2. 轮询：GET {job_url}/{job_id} → data.state ∈ {pending, running, done, failed}
   - running：data.extractProgress.{totalPages, extractedPages}
   - done：data.resultUrl.jsonUrl（一个 JSONL 文件 URL）
   - failed：data.errorMsg
3. 取结果：GET jsonUrl → 每行 JSON，result.layoutParsingResults[].markdown.text 为该页 markdown
   （PaddleOCR-VL 的 markdown 内联 HTML 表格，表格结构在 <table>…</table> 里）

base_url（config.yaml ocr.base_url）= 完整 jobs 端点（任务页「API_URL」原样）。
"""
import os
import re
import json
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any
import requests

log = logging.getLogger("ocr")

_TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)


class OCRError(Exception):
    pass


@dataclass
class OcrTable:
    page: int
    html: str = ""           # 表格 HTML（保留行列结构）
    markdown: str = ""


@dataclass
class OcrDoc:
    full_markdown: str = ""
    pages: List[str] = field(default_factory=list)     # 每页 markdown
    tables: List[OcrTable] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)  # 调试信息

    @property
    def page_count(self) -> int:
        return len(self.pages)


class PaddleOcrVL:
    def __init__(self, job_url, token, model="PaddleOCR-VL-1.6", optional_payload=None,
                 timeout=120, poll_interval=5, max_wait=1800, max_retries=3):
        self.job_url = (job_url or "").rstrip("/")
        self.token = token
        self.model = model
        self.optional_payload = optional_payload if optional_payload is not None else {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.max_retries = max_retries

    def _headers(self):
        # 官方示例为小写 bearer；不手动设 Content-Type，让 requests 自动加 multipart 边界
        return {"Authorization": f"bearer {self.token}"}

    # ---- 对外主入口 ----
    def parse_pdf(self, pdf_path: str, on_progress=None) -> OcrDoc:
        """on_progress(extracted, total, state)：轮询到进度时回调，用于前端实时展示。"""
        if not self.job_url:
            raise OCRError("ocr.base_url 为空：请填入 AI Studio 任务页的 API_URL（完整 jobs 端点）")
        if not self.token:
            raise OCRError("PADDLEOCR_ACCESS_TOKEN 未设置（检查 .env）")
        job_id = self._submit(pdf_path)
        log.info("OCR 任务已提交：jobId=%s，开始轮询…", job_id)
        jsonl_url = self._poll(job_id, on_progress)
        return self._fetch_result(jsonl_url)

    # ---- 1. 提交任务（本地文件 multipart）----
    def _submit(self, pdf_path: str) -> str:
        data = {"model": self.model, "optionalPayload": json.dumps(self.optional_payload)}
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with open(pdf_path, "rb") as f:
                    files = {"file": (os.path.basename(pdf_path), f)}
                    resp = requests.post(self.job_url, headers=self._headers(),
                                         data=data, files=files, timeout=self.timeout)
                if resp.status_code != 200:
                    raise OCRError(f"提交任务 HTTP {resp.status_code}: {resp.text[:300]}")
                j = resp.json()
                job_id = (j.get("data") or {}).get("jobId")
                if not job_id:
                    raise OCRError(f"提交任务返回无 jobId: {str(j)[:300]}")
                return job_id
            except Exception as e:  # noqa
                last_err = e
                log.warning("提交 OCR 任务失败(第%d/%d次): %s", attempt, self.max_retries, e)
                time.sleep(min(2 ** attempt, 15))
        raise OCRError(f"提交 OCR 任务连续 {self.max_retries} 次失败: {last_err}")

    # ---- 2. 轮询任务状态 ----
    def _poll(self, job_id: str, on_progress=None) -> str:
        url = f"{self.job_url}/{job_id}"
        waited = 0
        fails = 0
        while waited < self.max_wait:
            try:
                resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
                if resp.status_code != 200:
                    raise OCRError(f"查询任务 HTTP {resp.status_code}: {resp.text[:300]}")
                data = (resp.json() or {}).get("data") or {}
            except Exception as e:  # noqa
                fails += 1
                if fails > self.max_retries:
                    raise OCRError(f"轮询 OCR 任务连续失败: {e}")
                log.warning("轮询失败(第%d次)，稍后重试: %s", fails, e)
                time.sleep(self.poll_interval)
                waited += self.poll_interval
                continue
            fails = 0
            state = data.get("state")
            if state == "done":
                jsonl_url = (data.get("resultUrl") or {}).get("jsonUrl")
                if not jsonl_url:
                    raise OCRError(f"任务完成但无 resultUrl.jsonUrl: {str(data)[:300]}")
                log.info("OCR 任务完成：%s", (data.get("extractProgress") or {}))
                return jsonl_url
            if state == "failed":
                raise OCRError(f"OCR 任务失败: {data.get('errorMsg')}")
            prog = data.get("extractProgress") or {}
            log.info("OCR 任务 %s 状态=%s 进度=%s/%s", job_id, state,
                     prog.get("extractedPages"), prog.get("totalPages"))
            if on_progress:
                try:
                    on_progress(prog.get("extractedPages"), prog.get("totalPages"), state)
                except Exception:  # noqa  进度回调失败绝不影响 OCR 主流程
                    pass
            time.sleep(self.poll_interval)
            waited += self.poll_interval
        raise OCRError(f"OCR 任务 {job_id} 超过 {self.max_wait}s 仍未完成")

    # ---- 3. 下载并解析 JSONL 结果 ----
    def _fetch_result(self, jsonl_url: str) -> OcrDoc:
        resp = requests.get(jsonl_url, timeout=self.timeout)
        resp.raise_for_status()
        doc = OcrDoc(raw={"jsonl_url": jsonl_url})
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line).get("result", {})
            except json.JSONDecodeError:
                continue
            for res in result.get("layoutParsingResults", []):
                md = (res.get("markdown") or {}).get("text", "") or ""
                doc.pages.append(md)
                page_no = len(doc.pages)
                for html in _TABLE_RE.findall(md):
                    doc.tables.append(OcrTable(page=page_no, html=html))
        if not doc.pages:
            raise OCRError("OCR 结果为空（JSONL 未解析出任何页 markdown）")
        doc.full_markdown = "\n\n".join(
            f"<!-- 第{i+1}页 -->\n{p}" for i, p in enumerate(doc.pages))
        log.info("OCR 解析完成：%d 页，%d 个表格", doc.page_count, len(doc.tables))
        return doc


def build_ocr(cfg) -> PaddleOcrVL:
    o = cfg.ocr
    return PaddleOcrVL(
        job_url=o.get("base_url", ""),
        token=cfg.ocr_token,
        model=o.get("model", "PaddleOCR-VL-1.6"),
        optional_payload=o.get("optional_payload"),
        timeout=o.get("timeout", 120),
        poll_interval=o.get("poll_interval", 5),
        max_wait=o.get("max_wait", 1800),
        max_retries=o.get("max_retries", 3),
    )
