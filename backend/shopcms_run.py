"""shopcms 商家后台 站点入口（uvicorn 8040）。

仅挂载 shop API + 静态站，避免与主站首页路由冲突；
生产 nginx shopcms.bhzn.top → 127.0.0.1:8040（8030 已被既有服务占用，shop 用 8031）。
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .app.api.auth import router as auth_router
from .app.api.shop import cms_router, router as shop_router

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "shopcms_static"
PORT = int(os.environ.get("SHOPCMS_PORT", "8040"))

app = FastAPI(title="shopcms 商家后台")
app.include_router(auth_router, prefix="/auth")  # 同源登录：手机号密码登录
app.include_router(shop_router)
app.include_router(cms_router)


@app.get("/p/{product_id}")
def product_page(product_id: int) -> FileResponse:
    """商品落地页（推广链接指向这里）。"""
    return FileResponse(str(STATIC / "index.html"))


UPLOADS = ROOT / "shop_uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
app.mount("/shop-uploads", StaticFiles(directory=str(UPLOADS)), name="uploads")
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="site")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
