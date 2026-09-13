"""定时项目体检：每天 09:00 / 17:00 由 systemd timer 触发（幂等，可重复执行）。

在服务器上跑：`python3 -m backend.manage_checkup_cron`
- 按本机时区判断当前属于 0900 还是 1700 档
- 遍历所有公司、所有未归档项目，生成对应档位的体检（已存在的跳过）
"""
from __future__ import annotations

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
    hour = (now or datetime.now()).hour
    return "0900" if hour < 13 else "1700"


def main() -> int:
    from backend.app.api.manage import _build_checkup
    from backend.app.db import SessionLocal
    from backend.app.manage_models import MCheckup, MCompany, MProject

    slot = current_slot()
    today = date.today().isoformat()
    db = SessionLocal()
    created = skipped = 0
    try:
        for company in db.query(MCompany).filter(MCompany.status == "active").all():
            projects = (db.query(MProject)
                        .filter(MProject.company_id == company.id, MProject.status != "archived").all())
            for project in projects:
                exists = (db.query(MCheckup)
                          .filter(MCheckup.project_id == project.id, MCheckup.slot == slot,
                                  MCheckup.checked_on == today).first())
                if exists:
                    skipped += 1
                    continue
                db.add(_build_checkup(db, project, slot, today))
                created += 1
        db.commit()
        logger.info("[MANAGE-CHECKUP] slot=%s date=%s created=%s skipped=%s", slot, today, created, skipped)
    except Exception:
        db.rollback()
        logger.exception("[MANAGE-CHECKUP] failed")
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
