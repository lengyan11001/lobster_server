"""Run the isolated H5 chat app on its own port."""

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

_log_level_name = os.environ.get("H5_LOG_LEVEL", os.environ.get("LOG_LEVEL", "info")).strip().lower()
_log_level = getattr(logging, _log_level_name.upper(), logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backend.h5_run")

import uvicorn


if __name__ == "__main__":
    host = os.environ.get("H5_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("H5_PORT", "8010") or "8010")
    logger.info("[H5] starting isolated app host=%s port=%s", host, port)
    uvicorn.run(
        "backend.app.h5_main:app",
        host=host,
        port=port,
        log_level=_log_level_name,
    )
