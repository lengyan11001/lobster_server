"""Run the isolated manage app on its own port."""

from __future__ import annotations

import logging
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
os.chdir(_root)

from dotenv import load_dotenv

load_dotenv(os.path.join(_root, ".env"), override=False)

_level_name = os.environ.get("MANAGE_LOG_LEVEL", os.environ.get("LOG_LEVEL", "info")).strip().lower()
_level = getattr(logging, _level_name.upper(), logging.INFO)
logging.basicConfig(level=_level,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
try:
    from backend.app.core.log_retention import configure_daily_file_logging

    configure_daily_file_logging(_root, "manage", _level)
except Exception:
    pass

logger = logging.getLogger("backend.manage_run")

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("MANAGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("MANAGE_PORT", "8020") or "8020")
    logger.info("[MANAGE] starting isolated app host=%s port=%s", host, port)
    uvicorn.run("backend.app.manage_main:app", host=host, port=port, log_level=_level_name)
