"""shop 独立站 站点入口（uvicorn）。

本地预览/部署：python -m backend.shop_run  或  uvicorn backend.shop_run:app --port 8030
生产由 nginx shop.bhzn.top 反代到 127.0.0.1:8030。
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .create_app import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "shop_static"
PORT = int(os.environ.get("SHOP_PORT", "8030"))

app = create_app()
app.mount("/static", StaticFiles(directory=str(STATIC)), name="shop-static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC / "index.html"))


@app.get("/p/{product_id}")
def product_page(product_id: int) -> FileResponse:
    """商品落地页（P2 前端接入前，先复用同一个页面骨架）。"""
    return FileResponse(str(STATIC / "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
