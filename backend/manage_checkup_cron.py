"""定时项目体检：每天 09:00 / 17:00 由 systemd timer 触发（幂等，可重复执行）。

服务器上跑：`python3 -m backend.manage_checkup_cron`
- 按本机时区判断当前属于 0900 还是 1700 档
- 遍历所有公司、所有未归档项目生成体检（已存在的跳过）
- 默认走 AI（P7 提示词，早盘/收口 + 与上次对比）；设 MANAGE_CHECKUP_USE_AI=0 可退回规则版
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, datetime

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
os.chdir(_root)

from dotenv import load_dotenv

load_dotenv(os.path.join(_root, ".env"), override=False)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("backend.manage_checkup_cron")


def current_slot(now: datetime | None = None) -> str:
    return "0900" if (now or datetime.now()).hour < 13 else "1700"


def main() -> int:
    from backend.app.api.manage import run_checkups_for_company
    from backend.app.db import SessionLocal
    from backend.app.manage_models import MCompany

    slot = current_slot()
    today = date.today().isoformat()
    use_ai = (os.environ.get("MANAGE_CHECKUP_USE_AI", "1").strip().lower()
              not in ("0", "false", "no"))
    db = SessionLocal()
    created = ai_used = ai_failed = 0
    try:
        companies = db.query(MCompany).filter(MCompany.status == "active").all()
        for company in companies:
            result = asyncio.run(run_checkups_for_company(db, company, slot, today, use_ai=use_ai))
            created += result["created"]
            ai_used += result["ai_used"]
            ai_failed += result["ai_failed"]
        db.commit()
        logger.info("[MANAGE-CHECKUP] slot=%s date=%s companies=%s created=%s ai=%s ai_failed=%s",
                    slot, today, len(companies), created, ai_used, ai_failed)
    except Exception:
        db.rollback()
        logger.exception("[MANAGE-CHECKUP] failed")
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
