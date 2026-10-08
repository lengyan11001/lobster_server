"""视频计费：按上游回执结算 + 同段只扣一次 的回归测试（2026-09-18）。

背景：原来只有「提交阶段报错」才退款；任务受理后失败、或客户端拿到成片后判不可用，
预扣都照扣不退（09-18 那天一单失败的 3 段任务净扣 1820 积分，其中约 800 分拿不到成片）。

覆盖：
1. 回执状态/时长解析（dashscope wan3.0 的 output.task_status / output.usage.output_video_duration、grok 的扁平结构）；
2. 任务失败 → 全额自动退（幂等，重复轮询不会再退）；
3. 任务成功 + 按秒计费 → 按实际时长结算差额（预扣 10s=1200，实际 5s → 退 600）；
4. 任务成功 + 一次性计费（grok）→ 按预扣定案，不补不退；
5. 同一段分镜换渠道重试 → 上一笔未结算的预扣自动退回（同段只扣一次，幂等）；
6. 预扣行能从 hold key 改挂到上游 task id。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from backend.app.api import comfly_proxy

WAN = "wan3.0-video"
GROK = "grok-imagine-video-1.5"


@pytest.fixture
def proxy_env(monkeypatch, db_session_factory, patch_fuiou_settings):
    """把 proxy 的 SessionLocal 指到测试库，并静音审计日志。"""
    monkeypatch.setattr(comfly_proxy, "SessionLocal", db_session_factory)
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *args, **kwargs: None)
    assert comfly_proxy._should_deduct_credits() is True
    return db_session_factory


def _make_user(factory, credits: str = "10000") -> int:
    from backend.app.models import User

    db = factory()
    try:
        user = User(
            email=f"settle-{uuid.uuid4().hex[:8]}@test.local",
            hashed_password="x",
            credits=Decimal(credits),
            role="user",
            preferred_model="sutui",
            created_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return int(user.id)
    finally:
        db.close()


def _balance(factory, user_id: int) -> float:
    from backend.app.models import User

    db = factory()
    try:
        return float(db.query(User).filter(User.id == user_id).first().credits)
    finally:
        db.close()


def _ledger(factory, user_id: int):
    from backend.app.models import CreditLedger

    db = factory()
    try:
        rows = (
            db.query(CreditLedger)
            .filter(CreditLedger.user_id == user_id)
            .order_by(CreditLedger.id)
            .all()
        )
        return [(r.entry_type, float(r.delta), r.ref_type, r.ref_id, dict(r.meta or {})) for r in rows]
    finally:
        db.close()


def _charge(factory, user_id: int, *, model: str, amount: int, duration: float, segment_key: str = "", hold: str = ""):
    """按提交路由的方式建一笔预扣（带 hold key / 段 key）。"""
    hold_key = hold or comfly_proxy._video_charge_hold_key()
    pre = comfly_proxy._do_pre_deduct_by_user_id(
        user_id,
        amount,
        capability_id=comfly_proxy._CAPABILITY_FOR_BILLING,
        model=model,
        endpoint="video_submit",
        extra_meta=comfly_proxy._video_charge_extra_meta(
            {"duration": duration}, segment_key=segment_key, hold_key=hold_key
        ),
        ref_type=comfly_proxy._VIDEO_CHARGE_REF_TYPE,
        ref_id=hold_key,
    )
    return pre, hold_key


# —— 1. 回执解析 ——

def test_receipt_status_and_seconds_parsing():
    dashscope = {
        "request_id": "r1",
        "output": {
            "task_id": "t1",
            "task_status": "SUCCEEDED",
            "usage": {"output_video_duration": 5, "SR": 720, "ratio": "9:16"},
        },
    }
    assert comfly_proxy._video_receipt_status(dashscope) == "succeeded"
    assert comfly_proxy._video_receipt_seconds(dashscope) == 5.0

    assert comfly_proxy._video_receipt_status({"status": "FAILED", "error": "boom"}) == "failed"
    assert comfly_proxy._video_receipt_status({"id": "x", "video_url": "http://a/b.mp4"}) == ""
    assert comfly_proxy._video_receipt_seconds({"duration": "10s"}) == 10.0


# —— 2. 任务失败 → 全额退（幂等）——

def test_failed_task_refunds_full_charge_once(proxy_env):
    user_id = _make_user(proxy_env)
    pre, hold = _charge(proxy_env, user_id, model=WAN, amount=1200, duration=10)
    assert float(pre) == 1200.0
    comfly_proxy._bind_video_charge_to_task(user_id=user_id, hold_key=hold, task_id="task_fail_1")

    summary = comfly_proxy._settle_video_charge_by_task(
        user_id=user_id, task_id="task_fail_1", payload={"output": {"task_status": "FAILED"}}
    )
    assert summary and summary["action"] == "refund"
    assert _balance(proxy_env, user_id) == 10000.0

    # 幂等：重复轮询不会再退
    assert comfly_proxy._settle_video_charge_by_task(
        user_id=user_id, task_id="task_fail_1", payload={"output": {"task_status": "FAILED"}}
    ) is None
    assert _balance(proxy_env, user_id) == 10000.0
    refunds = [r for r in _ledger(proxy_env, user_id) if r[0] == "refund"]
    assert len(refunds) == 1 and refunds[0][1] == 1200.0


# —— 3. 成功 + 按秒计费 → 按实际时长结算 ——

def test_succeeded_per_second_task_settles_by_actual_duration(proxy_env):
    user_id = _make_user(proxy_env)
    _, hold = _charge(proxy_env, user_id, model=WAN, amount=1200, duration=10)  # 预扣 10 秒
    comfly_proxy._bind_video_charge_to_task(user_id=user_id, hold_key=hold, task_id="task_ok_1")

    summary = comfly_proxy._settle_video_charge_by_task(
        user_id=user_id,
        task_id="task_ok_1",
        payload={"output": {"task_status": "SUCCEEDED", "usage": {"output_video_duration": 5}}},
        model_hint=WAN,
    )
    assert summary and summary["action"] == "settle"
    assert summary["actual_seconds"] == 5.0
    assert _balance(proxy_env, user_id) == 9400.0  # 10000 - 1200 + 600
    settles = [r for r in _ledger(proxy_env, user_id) if r[0] == "refund" and r[3] == "task_ok_1"]
    assert len(settles) == 1 and settles[0][1] == 600.0

    # 幂等
    assert comfly_proxy._settle_video_charge_by_task(
        user_id=user_id,
        task_id="task_ok_1",
        payload={"output": {"task_status": "SUCCEEDED", "usage": {"output_video_duration": 5}}},
    ) is None
    assert _balance(proxy_env, user_id) == 9400.0


# —— 4. 一次性计费：按预扣定案 ——

def test_succeeded_per_call_model_stays_charged(proxy_env):
    user_id = _make_user(proxy_env)
    _, hold = _charge(proxy_env, user_id, model=GROK, amount=320, duration=10)
    comfly_proxy._bind_video_charge_to_task(user_id=user_id, hold_key=hold, task_id="task_grok_1")

    summary = comfly_proxy._settle_video_charge_by_task(
        user_id=user_id, task_id="task_grok_1", payload={"status": "succeeded"}, model_hint=GROK
    )
    assert summary and summary["action"] == "settled_as_charged"
    assert _balance(proxy_env, user_id) == 9680.0  # 只扣 320，不补不退
    assert len([r for r in _ledger(proxy_env, user_id) if r[0] in {"refund", "settle"}]) == 0


# —— 5. 同段只扣一次 ——

def test_supersede_previous_charge_for_same_segment(proxy_env):
    user_id = _make_user(proxy_env)
    segment = "run_20260918_000000:seg01"
    _, hold_a = _charge(proxy_env, user_id, model=WAN, amount=1200, duration=10, segment_key=segment)
    comfly_proxy._bind_video_charge_to_task(user_id=user_id, hold_key=hold_a, task_id="task_a")
    assert _balance(proxy_env, user_id) == 8800.0

    # 换渠道重试同一段 → 上一笔退回
    refunded = comfly_proxy._supersede_previous_video_charge(user_id=user_id, segment_key=segment)
    assert float(refunded) == 1200.0
    assert _balance(proxy_env, user_id) == 10000.0
    # 幂等：同一段不会再退第二次
    assert comfly_proxy._supersede_previous_video_charge(user_id=user_id, segment_key=segment) is None
    assert _balance(proxy_env, user_id) == 10000.0


def test_supersede_ignores_other_segments_and_settled_charges(proxy_env):
    user_id = _make_user(proxy_env)
    _, hold_a = _charge(proxy_env, user_id, model=WAN, amount=1200, duration=10, segment_key="run:seg01")
    comfly_proxy._bind_video_charge_to_task(user_id=user_id, hold_key=hold_a, task_id="task_seg1")
    # 已结算过的段不再被顶掉
    comfly_proxy._settle_video_charge_by_task(
        user_id=user_id, task_id="task_seg1", payload={"output": {"task_status": "SUCCEEDED", "usage": {"output_video_duration": 10}}}
    )
    assert comfly_proxy._supersede_previous_video_charge(user_id=user_id, segment_key="run:seg01") is None
    # 别的段不受影响
    assert comfly_proxy._supersede_previous_video_charge(user_id=user_id, segment_key="run:seg02") is None
    assert _balance(proxy_env, user_id) == 8800.0


# —— 6. hold key → task id ——

def test_bind_video_charge_to_task_rewrites_ref(proxy_env):
    user_id = _make_user(proxy_env)
    _, hold = _charge(proxy_env, user_id, model=WAN, amount=600, duration=5, segment_key="run:seg03")
    comfly_proxy._bind_video_charge_to_task(user_id=user_id, hold_key=hold, task_id="task_bound_1")

    rows = _ledger(proxy_env, user_id)
    assert rows[0][0] == "pre_deduct"
    assert rows[0][2] == comfly_proxy._VIDEO_CHARGE_REF_TYPE
    assert rows[0][3] == "task_bound_1"
    assert rows[0][4].get("generation_task_id") == "task_bound_1"
    assert rows[0][4].get("video_segment_key") == "run:seg03"
