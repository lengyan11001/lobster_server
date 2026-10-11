"""manage 飞书播报定时任务：每天 09:00 今日任务、17:00 逾期提醒。

服务器上跑：`python3 -m backend.manage_notify_cron`
- 09:00 档：每个在用公司发一张「今日任务」卡片（配了自建应用再给每个员工私聊自己的那份）
- 17:00 档：只发逾期清单（没有逾期不发，避免刷屏；项目体检卡片由 manage_checkup_cron 发）
- 没配 MANAGE_FEISHU_WEBHOOK / FEISHU_APP_* 时直接跳过，不影响任何业务
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
os.chdir(_root)

from dotenv import load_dotenv

load_dotenv(os.path.join(_root, ".env"), override=False)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("backend.manage_notify_cron")


def current_slot(now: datetime | None = None) -> str:
    return "0900" if (now or datetime.now()).hour < 13 else "1700"


def main() -> int:
    from backend.app.db import SessionLocal
    from backend.app.manage_models import MCompany
    from backend.app.services import feishu_bot, manage_notify

    if not feishu_bot.webhook_enabled() and not feishu_bot.app_enabled():
        logger.info("[MANAGE-NOTIFY] feishu 未配置，跳过（配 MANAGE_FEISHU_WEBHOOK 或 FEISHU_APP_ID/SECRET 即生效）")
        return 0

    slot = current_slot()
    today = manage_notify.today_str()
    db = SessionLocal()
    try:
        if slot == "0900":
            result = asyncio.run(manage_notify.notify_all_daily(db, today=today))
            logger.info("[MANAGE-NOTIFY] slot=0900 date=%s %s", today, result)
        else:
            companies = db.query(MCompany).filter(MCompany.status == "active").all()
            sent = 0
            for company in companies:
                res = asyncio.run(manage_notify.notify_overdue_only(db, company, today=today))
                if res.get("ok"):
                    sent += 1
            logger.info("[MANAGE-NOTIFY] slot=1700 date=%s companies=%s overdue_pushed=%s",
                        today, len(companies), sent)
    except Exception:
        logger.exception("[MANAGE-NOTIFY] failed")
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
