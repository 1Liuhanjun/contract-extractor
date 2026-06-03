"""
PaddleOCR API client for turning PDF/image files into text.

Uses the official asynchronous API:
  POST /api/v2/ocr/jobs
  GET  /api/v2/ocr/jobs/{jobId}
"""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid

try:
    from dotenv import load_dotenv

    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except ImportError:
    pass


DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


class OCRClientError(Exception):
    """Raised when PaddleOCR fails or returns an unexpected response."""


class PaddleOCRClient:
    def __init__(self, token=None, model=None, job_url=None, poll_interval=None, poll_timeout=None):
        self.token = token or os.environ.get("PADDLEOCR_ACCESS_TOKEN", "")
        self.model = model or os.environ.get("PADDLEOCR_MODEL", "PaddleOCR-VL-1.5")
        self.job_url = (job_url or os.environ.get("PADDLEOCR_JOB_URL", DEFAULT_JOB_URL)).rstrip("/")
        self.poll_interval = int(poll_interval or os.environ.get("PADDLEOCR_POLL_INTERVAL", "5"))
        self.poll_timeout = int(poll_timeout or os.environ.get("PADDLEOCR_POLL_TIMEOUT", "900"))

        if not self.token:
            raise OCRClientError("PADDLEOCR_ACCESS_TOKEN 未配置，请在 .env 中填写")

    def extract_text_from_file(self, file_path, save_dir=None):
        job_id = self.submit_file(file_path)
        status = self.wait_for_result(job_id)
        text, raw_jsonl = self.download_text(status)

        saved_text_path = None
        saved_jsonl_path = None
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(file_path))[0]
            safe_base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in base)[:80] or "ocr"
            saved_text_path = os.path.join(save_dir, f"{safe_base}_{job_id}.md")
            saved_jsonl_path = os.path.join(save_dir, f"{safe_base}_{job_id}.jsonl")
            with open(saved_text_path, "w", encoding="utf-8") as f:
                f.write(text)
            with open(saved_jsonl_path, "w", encoding="utf-8") as f:
                f.write(raw_jsonl)

        return {
            "job_id": job_id,
            "model": self.model,
            "text": text,
            "status": status,
            "saved_text_path": saved_text_path,
            "saved_jsonl_path": saved_jsonl_path,
        }

    def submit_file(self, file_path):
        if not os.path.exists(file_path):
            raise OCRClientError(f"OCR 文件不存在: {file_path}")

        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
            "prettifyMarkdown": True,
        }
        page_ranges = os.environ.get("PADDLEOCR_PAGE_RANGES", "").strip()
        fields = {
            "model": self.model,
            "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
        }
        if page_ranges:
            fields["pageRanges"] = page_ranges

        body, content_type = self._build_multipart(fields, file_path)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": content_type,
        }
        data = self._request_json(self.job_url, method="POST", data=body, headers=headers, timeout=300)
        job_id = data.get("data", {}).get("jobId")
        if not job_id:
            raise OCRClientError(f"OCR 提交失败，未返回 jobId: {data}")
        print(f"  [OCR] 已提交 PaddleOCR 任务: {job_id}")
        return job_id

    def wait_for_result(self, job_id):
        deadline = time.time() + self.poll_timeout
        url = f"{self.job_url}/{job_id}"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

        while time.time() < deadline:
            data = self._request_json(url, method="GET", headers=headers, timeout=60)
            payload = data.get("data", {})
            state = payload.get("state", "")

            if state == "done":
                print(f"  [OCR] 任务完成: {job_id}")
                return payload
            if state == "failed":
                raise OCRClientError(payload.get("errorMsg") or f"OCR 任务失败: {job_id}")

            progress = payload.get("extractProgress", {}) or {}
            total = progress.get("totalPages", "?")
            done = progress.get("extractedPages", "?")
            print(f"  [OCR] 状态 {state or 'unknown'}，进度 {done}/{total}")
            time.sleep(self.poll_interval)

        raise OCRClientError(f"OCR 任务超时: {job_id}")

    def download_text(self, status_payload):
        result_url = status_payload.get("resultUrl", {}) or {}
        json_url = result_url.get("jsonUrl")
        markdown_url = result_url.get("markdownUrl")

        if markdown_url:
            try:
                text = self._download_text(markdown_url)
                if text.strip():
                    return text, ""
            except OCRClientError:
                pass

        if not json_url:
            raise OCRClientError("OCR 结果缺少 jsonUrl/markdownUrl")

        raw_jsonl = self._download_text(json_url)
        text = self._extract_markdown_from_jsonl(raw_jsonl)
        if not text.strip():
            raise OCRClientError("OCR 结果为空，未能提取 Markdown 文本")
        return text, raw_jsonl

    def _build_multipart(self, fields, file_path):
        boundary = f"----ContractOCR{uuid.uuid4().hex}"
        parts = []
        for name, value in fields.items():
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            parts.append(str(value).encode("utf-8"))
            parts.append(b"\r\n")

        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        ext = os.path.splitext(file_path)[1] or ".bin"
        upload_name = f"upload{ext}"
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="file"; filename="{upload_name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(file_bytes)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _request_json(self, url, method="GET", data=None, headers=None, timeout=120):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise OCRClientError(f"PaddleOCR HTTP {e.code}: {body[:500]}") from e
        except Exception as e:
            raise OCRClientError(f"PaddleOCR 请求失败: {e}") from e

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            raise OCRClientError(f"PaddleOCR 返回非 JSON: {raw[:200]}") from e

        code = result.get("code", 0)
        if code not in (0, None):
            raise OCRClientError(f"PaddleOCR 返回错误 code={code}: {result.get('msg', '')}")
        return result

    def _download_text(self, url):
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            raise OCRClientError(f"OCR 结果下载失败: {e}") from e

    def _extract_markdown_from_jsonl(self, raw_jsonl):
        chunks = []
        for line in raw_jsonl.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            result = obj.get("result", obj)

            for item in result.get("layoutParsingResults", []) or []:
                markdown = item.get("markdown", {}) or {}
                text = markdown.get("text", "")
                if text:
                    chunks.append(text)

            for item in result.get("ocrResults", []) or []:
                text = item.get("text") or item.get("ocrText") or ""
                if text:
                    chunks.append(text)

        return "\n\n".join(chunks)
