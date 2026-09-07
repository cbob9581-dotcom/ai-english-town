"""启动 api 的入口：自动把本地包（scene_compiler / scene_schema）加入 sys.path，
再起 uvicorn。解决绕过 uv 直接用 venv 运行时的 import 问题；用法：

    python apps/api/run_api.py            # 默认 127.0.0.1:8000
    $env:PORT=8000; python apps/api/run_api.py

LLM key 等配置请放 apps/api/.env（settings.py 会自动加载，.env 不入库）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent            # apps/api
_ROOT = _API_DIR.parent.parent                        # 仓库根

for p in (
    _API_DIR,                                          # app 包
    _ROOT / "packages" / "scene-compiler",             # scene_compiler
    _ROOT / "packages" / "scene-schema" / "python",    # scene_schema
):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        workers=1,
    )
