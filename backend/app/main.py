from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    _root = Path(__file__).resolve().parents[2]
    load_dotenv(_root / ".env", override=False)
    _log_level_name = os.environ.get("LOG_LEVEL", "debug").strip().lower()
    _log_level = getattr(logging, _log_level_name.upper(), logging.DEBUG)

    from .core.log_retention import cleanup_diagnostics_uploads, configure_daily_file_logging

    configure_daily_file_logging(_root, "app", _log_level)
    cleanup_diagnostics_uploads(_root)
except Exception:
    pass

from .create_app import app

__all__ = ["app"]
