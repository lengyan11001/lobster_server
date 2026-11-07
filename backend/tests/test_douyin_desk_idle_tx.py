import asyncio
import decimal
import types

from backend.app.api import douyin_platform_information_desk as api
from backend.app.services import douyin_desk_billing as billing


class FakeDB:
    def __init__(self):
        self.calls = []

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def add(self, row):
        self.calls.append(("add", row))

    def refresh(self, user):
        pass


def test_imitation_commits_before_slow_upstream_and_survives_billing_error(monkeypatch):
    db = FakeDB()
    user = types.SimpleNamespace(id=316, credits=decimal.Decimal("10000"))

    monkeypatch.setattr(api, "_require_information_desk_access", lambda u, d: None)
    monkeypatch.setattr(billing, "check_balance", lambda d, u, c: db.calls.append("check"))

    async def fake_prepare(*args, **kwargs):
        db.calls.append("prepare")
        return {
            "ok": True,
            "task_id": "upstream-task-1",
            "input_seconds": 5,
            "output_seconds": 5,
            "resolution": "720P",
            "provider": "videoedit",
            "model": "wan3.0-video-prime",
        }

    monkeypatch.setattr(api, "prepare_imitation", fake_prepare)
    monkeypatch.setattr(
        billing, "estimate_imitation",
        lambda *args, **kwargs: {"credits": 10, "billable_seconds": 10, "resolution": "720P"},
    )

    def fake_deduct(*args, **kwargs):
        raise RuntimeError("idle-in-transaction timeout")

    monkeypatch.setattr(billing, "deduct", fake_deduct)

    body = api.ImitationIn(image_url="https://x/a.png", video_url="https://x/v.mp4")
    result = asyncio.run(api.create_information_desk_imitation(body, user, db))

    assert db.calls.index("commit") < db.calls.index("prepare")
    assert "rollback" in db.calls
    assert any(isinstance(item, tuple) and item[0] == "add" for item in db.calls)
    assert result["billing"]["credits_charged"] == 0
    assert result["task_id"] == "upstream-task-1"
