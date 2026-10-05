"""生成任务对账补偿：上游 completed 补登记 / failed 与 404 退款。"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from backend.app.services.generation_reconciler import classify_upstream


def test_classify_upstream_verdicts():
    completed = {"code": 200, "data": {"status": "completed",
                                       "output": {"images": [{"url": "https://cdn.test/a.png"}]}}}
    assert classify_upstream(completed)[0] == "completed"
    assert classify_upstream(completed)[1] == "https://cdn.test/a.png"

    failed = {"code": 200, "data": {"status": "failed", "output": {"error": "PROVIDER_MODERATION_ERROR: x"}}}
    verdict, url, detail = classify_upstream(failed)
    assert verdict == "failed" and url == "" and "MODERATION" in detail

    assert classify_upstream({"detail": "任务不存在"})[0] == "missing"
    assert classify_upstream({"code": 404, "detail": "x"})[0] == "missing"
    assert classify_upstream({"code": 200, "data": {"status": "processing"}})[0] == "pending"
    assert classify_upstream(None)[0] == "pending"


def test_reconcile_canvas_once_refunds_failed_and_registers_completed(monkeypatch):
    """上游 failed → 退预扣；上游 completed → 登记产物。"""
    from backend.app.api import canvas_hub, canvas_proxy
    from backend.app.services import generation_reconciler as rec

    rows = [
        {"id": 1, "user_id": 42, "task_id": "task-failed", "model": "fal-ai/nano-banana-2",
         "path": "api/v3/tasks/create", "charged": 72.0, "created_at": 0.0},
        {"id": 2, "user_id": 42, "task_id": "task-done", "model": "openai/gpt-image-2",
         "path": "api/v3/tasks/create", "charged": 6.0, "created_at": 0.0},
    ]
    seen = {}

    monkeypatch.setattr(rec, "_due_canvas_rows", lambda db, limit: rows[:limit])

    async def fake_apiz(method, path, body=None, **kwargs):
        task_id = (body or {}).get("task_id")
        if task_id == "task-failed":
            return {"code": 200, "data": {"status": "failed", "output": {"error": "boom"}}}
        return {"code": 200, "data": {"status": "completed",
                                      "output": {"images": [{"url": "https://cdn.test/x.png"}]}}}

    monkeypatch.setattr(canvas_hub, "apiz_json", fake_apiz)
    monkeypatch.setattr(canvas_hub, "sync_canvas_task", lambda *a, **k: seen.setdefault("sync", []).append(k.get("status")))
    monkeypatch.setattr(canvas_hub, "mark_canvas_task_refunded", lambda *a, **k: seen.setdefault("marked", []).append(k.get("task_id")))
    monkeypatch.setattr(canvas_hub, "register_content_record", lambda *a, **k: seen.setdefault("registered", []).append(a[2]))
    monkeypatch.setattr(canvas_proxy, "refund_canvas", lambda db, user, amount, model, path, reason: seen.setdefault("refunds", []).append((float(amount), reason)))

    class _User:
        id = 42

    class _Query:
        def filter(self, *a, **k):
            return self

        def first(self):
            return _User()

    class _Db:
        def query(self, *a, **k):
            return _Query()

        def close(self):
            pass

    monkeypatch.setattr("backend.app.db.SessionLocal", lambda: _Db())
    monkeypatch.setattr(rec, "json_dumps_safe", lambda value: "{}")
    monkeypatch.setenv("GENERATION_RECONCILE_GAP_SECONDS", "0")

    stats = asyncio.run(rec.reconcile_canvas_once(limit=5))

    assert stats["checked"] == 2 and stats["failed"] == 1 and stats["completed"] == 1
    assert seen["refunds"] == [(72.0, "对账补退（上游 failed）")]
    assert seen["marked"] == ["task-failed"]
    assert seen["registered"] == ["https://cdn.test/x.png"]
    assert "completed" in seen["sync"]


def test_reconcile_respects_batch_limit(monkeypatch):
    from backend.app.services import generation_reconciler as rec

    seen = {}

    def fake_due(db, limit):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(rec, "_due_canvas_rows", fake_due)

    class _Db:
        def close(self):
            pass

    monkeypatch.setattr("backend.app.db.SessionLocal", lambda: _Db())
    stats = asyncio.run(rec.reconcile_canvas_once(limit=7))
    assert seen["limit"] == 7 and stats["checked"] == 0
