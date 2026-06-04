# Web 前端 · 合同读取智能体

深色科技感前端 + FastAPI 服务，包裹现有 `pipeline.process_pdf` 七层流水线。
**本地单机运行**：上传 PDF → 实时看到 OCR→分类→抽取→计算→校验→写表→报告 的进度 → 下载主台账 Excel / 复核报告。

## 架构
```
浏览器 (React+Vite+Framer Motion)
  POST /api/upload            上传 1~N 个 PDF，返回 job_id
  GET  /api/jobs/{id}/stream  SSE 实时进度（七层事件）
  GET  /api/history           历史（读 output/_index.json）
  GET  /api/download/excel    主台账 Excel
  GET  /api/download/report/{contract_id}  复核报告 md
        │
FastAPI (web/server.py) ── 后台线程跑 pipeline.process_pdf(..., progress=cb)
        │                    事件 → queue.Queue → SSE 下发
现有后端：app_config / providers / layers / pipeline（逻辑未改）
```

## 准备
1. 后端依赖：`pip install -r requirements.txt`（已含 fastapi/uvicorn/python-multipart）
2. `.env` 填好 `DEEPSEEK_API_KEY`、`PADDLEOCR_ACCESS_TOKEN`（同 CLI）
3. 前端依赖（需要 Node 18+）：`cd web/frontend && npm install`

## 开发模式（前后端分离，热重载）
两个终端，均在「方案2 ocr/代码」目录下：
```bash
# 终端 1：后端
uvicorn web.server:app --reload --port 8000
#   或：python web/run_web.py --reload

# 终端 2：前端（自带 /api 代理到 8000）
cd web/frontend && npm run dev
```
浏览器打开 Vite 提示的地址（默认 http://localhost:5173）。

## 生产模式（单端口）
```bash
cd web/frontend && npm run build      # 生成 dist/
cd ../.. && python web/run_web.py     # http://127.0.0.1:8000 直接访问
```
FastAPI 会自动托管 `web/frontend/dist/`，前后端同端口。

## 说明
- 不改动 `layers/*`、`providers/*`、`schemas.py`、`config.yaml` 等；`pipeline.process_pdf`
  仅新增可选 `progress` 回调，CLI（`python run.py`）行为不变。
- 多文件批量为「全局串行」处理，保证 `_index.json`/Excel 写入安全；UI 同时展示多条流水线。
- 去重命中、非邮政合同会以「已跳过」展示并说明原因。
```
