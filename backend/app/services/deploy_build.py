"""部署构建标识：给前端做「自动失效缓存」用。

页面把它写在 window.__ADMIN_BUILD__ / window.__H5_BUILD__，并定时问对应的
/api/build；值变了就 location.reload()。H5 的 js/css 引用也按这个值重写 ?v=，
所以每次部署浏览器都会当成新资源去取 —— 用户不需要手动刷新或清缓存。
"""
from __future__ import annotations

from pathlib import Path

_CACHE: dict = {}
_ROOT = Path(__file__).resolve().parents[3]
_WATCH_FILES = (
    "backend/app/static/admin.html",
    "h5_static/index.html",
    "h5_static/h5-app.js",
    "h5_static/h5-app.css",
)


def deploy_build_id() -> str:
    cached = str(_CACHE.get("id") or "")
    if cached:
        return cached
    commit = ""
    try:
        commit = (_ROOT / ".deploy_rollback_commit").read_text(encoding="utf-8").strip()[:12]
    except OSError:
        commit = ""
    newest = 0
    for rel in _WATCH_FILES:
        try:
            newest = max(newest, int((_ROOT / rel).stat().st_mtime))
        except OSError:
            continue
    build = "%s-%d" % (commit or "nogit", newest)
    _CACHE["id"] = build
    return build
