from __future__ import annotations

from datetime import datetime, timedelta


def test_chat_retention_prunes_transient_events_but_keeps_active_history(
    db_session, db_session_factory, test_user, monkeypatch
):
    from backend.app.models import H5ChatEvent, H5ChatMessage, H5ChatSession
    from backend.app.services import h5_chat_retention

    old = datetime.utcnow() - timedelta(days=10)
    session = H5ChatSession(
        id="retention-active-session",
        user_id=test_user.id,
        title="保留的会话",
        permission_mode="confirm",
        created_at=old,
        updated_at=old,
    )
    message = H5ChatMessage(
        id="retention-completed-message",
        user_id=test_user.id,
        session_id=session.id,
        mode="mastra",
        content="原始问题必须保留",
        reply_text="最终回答必须保留",
        status="completed",
        created_at=old,
        updated_at=old,
        finished_at=old,
    )
    transient = H5ChatEvent(
        message_id=message.id,
        user_id=test_user.id,
        event_type="delta",
        payload={"text": "逐字增量"},
        created_at=old,
    )
    final = H5ChatEvent(
        message_id=message.id,
        user_id=test_user.id,
        event_type="final",
        payload={"reply_text": message.reply_text},
        created_at=old,
    )
    db_session.add_all([session, message, transient, final])
    db_session.commit()
    monkeypatch.setattr(h5_chat_retention, "SessionLocal", db_session_factory)

    result = h5_chat_retention.cleanup_h5_chat_storage_sync()

    assert result["transient_events"] == 1
    with db_session_factory() as db:
        assert db.query(H5ChatMessage).filter(H5ChatMessage.id == message.id).count() == 1
        assert db.query(H5ChatEvent).filter(H5ChatEvent.message_id == message.id).count() == 1
        assert db.query(H5ChatEvent).filter(H5ChatEvent.message_id == message.id).one().event_type == "final"
