from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import ShanjianDigitalHumanVideoTask, UserContentRecord

logger = logging.getLogger(__name__)

SOURCE = 'shanjian_digital_human'


def _text(value, limit: int = 4000) -> str:
    return str(value or '').strip()[:limit]


def upsert_failed_record(db: Session, *, user_id: int, row: ShanjianDigitalHumanVideoTask) -> None:
    '''数字人生成失败也要在「作品」页留一条记录，否则用户跳过去什么都看不到。'''
    source_id = _text(row.task_id, 128) or str(row.id)
    if not source_id:
        return
    try:
        rec = (
            db.query(UserContentRecord)
            .filter(
                UserContentRecord.user_id == int(user_id),
                UserContentRecord.source == SOURCE,
                UserContentRecord.source_id == source_id,
            )
            .first()
        )
        if rec is not None and str(rec.status or '').lower() in {'failed', 'completed', 'succeed'}:
            return
        now = datetime.utcnow()
        if rec is None:
            rec = UserContentRecord(
                user_id=int(user_id),
                source=SOURCE,
                source_id=source_id,
                kind='video',
                created_at=now,
                updated_at=now,
                source_created_at=row.created_at or now,
            )
            db.add(rec)
        err = _text(row.error_message, 1000) or '生成失败，积分已退回'
        rec.title = _text(row.title, 200) or '数字人口播'
        rec.content = _text(row.text, 5000)
        rec.summary = ('生成失败：' + err)[:4000]
        rec.cover_url = _text(row.cover_url, 4096) or rec.cover_url
        rec.status = 'failed'
        meta = rec.meta if isinstance(rec.meta, dict) else {}
        meta.update(
            {
                'action': 'shanjian_digital_human_video',
                'task_id': _text(row.task_id, 128),
                'error': err,
                'failed_at': now.isoformat(timespec='seconds'),
            }
        )
        rec.meta = meta
        rec.updated_at = now
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning('[shanjian] write failed content record failed: %s', str(exc)[:200])
