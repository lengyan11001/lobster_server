from decimal import Decimal

from backend.app.api.comfly_proxy import _VIDEO_CHARGE_REF_TYPE, _mark_video_charge_refunded
from backend.app.models import CreditLedger


def test_mark_video_charge_refunded_is_idempotent(db_session, test_user):
    row = CreditLedger(
        user_id=test_user.id,
        delta=Decimal("-160.0000"),
        balance_after=Decimal("100.0000"),
        entry_type="pre_deduct",
        ref_type=_VIDEO_CHARGE_REF_TYPE,
        ref_id="vc-test-hold",
        meta={
            "video_segment_key": "run_20261118_020130:seg01",
            "model": "grok-video-3",
            "endpoint": "openmind_video_submit",
        },
    )
    db_session.add(row)
    db_session.commit()

    assert _mark_video_charge_refunded(
        db_session,
        user_id=test_user.id,
        hold_key="vc-test-hold",
        segment_key="run_20261118_020130:seg01",
        reason="openmind 503",
    ) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.meta["video_settle_action"] == "refunded"
    assert row.meta.get("video_settled_at")
    assert row.meta["video_refund_reason"] == "openmind 503"

    assert _mark_video_charge_refunded(
        db_session,
        user_id=test_user.id,
        hold_key="vc-test-hold",
        segment_key="run_20261118_020130:seg01",
        reason="second refund must be skipped",
    ) is False


def test_mark_video_charge_refunded_by_segment_when_holds_bound_to_task(db_session, test_user):
    row = CreditLedger(
        user_id=test_user.id,
        delta=Decimal("-320.0000"),
        balance_after=Decimal("100.0000"),
        entry_type="pre_deduct",
        ref_type=_VIDEO_CHARGE_REF_TYPE,
        ref_id="upstream-task-123",
        meta={"video_segment_key": "run_20261118_030000:seg02"},
    )
    db_session.add(row)
    db_session.commit()

    assert _mark_video_charge_refunded(
        db_session,
        user_id=test_user.id,
        hold_key="",
        segment_key="run_20261118_030000:seg02",
        reason="task failed",
    ) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.meta["video_settle_action"] == "refunded"
