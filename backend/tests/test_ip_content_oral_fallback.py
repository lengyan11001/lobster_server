"""老客户端不传时长时，回过 IP 设置里的口播预估时长。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import ip_content_studio as studio
from backend.app.models import IPContentScheduleTemplate


def _db():
    engine = create_engine("sqlite://")
    IPContentScheduleTemplate.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _row(db, **kwargs):
    row = IPContentScheduleTemplate(
        user_id=kwargs.get("user_id", 1),
        installation_id="",
        name=kwargs.get("name", studio._PERSONAL_DEFAULT_TEMPLATE_NAME),
        status="active",
        meta=kwargs.get("meta") or {},
    )
    db.add(row)
    db.commit()
    return row


def test_fallback_reads_personal_default_seconds():
    db = _db()
    _row(db, meta={"digital_human_script_seconds": 30})
    assert studio._resolve_oral_target_seconds(db, 1) == 30


def test_fallback_follows_current_template_id():
    db = _db()
    current = _row(db, name="我的模板", meta={"digital_human_script_seconds": 45})
    _row(db, meta={"current_template_id": current.id})
    assert studio._resolve_oral_target_seconds(db, 1) == 45


def test_fallback_zero_when_not_configured():
    db = _db()
    _row(db, meta={})
    assert studio._resolve_oral_target_seconds(db, 1) == 0


def test_fallback_ignores_out_of_range():
    db = _db()
    _row(db, meta={"digital_human_script_seconds": 9999})
    assert studio._resolve_oral_target_seconds(db, 1) == 0
