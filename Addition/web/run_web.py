"""一键启动 Web 服务（等价于 `uvicorn web.server:app --port 8000`）。

用法（在「方案2 ocr/代码」目录下）：
    python web/run_web.py            # 启动，默认 8000 端口
    python web/run_web.py --port 9000 --reload

生产前别忘了先构建前端：
    cd web/frontend && npm install && npm run build
构建产物 dist/ 会被本服务自动托管；不构建则只暴露 /api，前端走 `npm run dev`。
"""
import sys
import argparse
from pathlib import Path

# 保证能 import 到 web 包与后端模块
CODE_DIR = Path(__file__).resolve().parent.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))


def main():
    ap = argparse.ArgumentParser(description="合同读取智能体 Web 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="开发热重载")
    args = ap.parse_args()

    import uvicorn
    uvicorn.run("web.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
