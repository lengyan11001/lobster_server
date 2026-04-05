#!/usr/bin/env python3
"""手动跑一次速推对账（远端余额变动 vs 本地带 _recon 的流水），打印 JSON。"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    import os

    os.chdir(ROOT)
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass
    from backend.app.services.sutui_reconcile import run_sutui_reconcile_sync

    out = run_sutui_reconcile_sync()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
