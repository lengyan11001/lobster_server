# -*- coding: utf-8 -*-
"""闪剪并发满时不判失败，排队重试（2026-10-21）。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.app.api import shanjian_digital_human as sj


def test_concurrency_limit_is_queued_until_slot_free(monkeypatch):
    calls = {"n": 0}

    async def fake_post(path, token, payload):
        calls["n"] += 1
        if calls["n"] < 3:
            raise HTTPException(status_code=502, detail="闪剪错误 Concurrency.Limit: 当前并发 6, 超出了权益并发 5")
        return {"code": "Succeed", "data": {"taskId": "t1"}}

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(sj, "_post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(sj._post_virtualman_with_queue("token", {"title": "x"}))
    assert result["data"]["taskId"] == "t1"
    assert calls["n"] == 3


def test_other_errors_not_retried(monkeypatch):
    calls = {"n": 0}

    async def fake_post(path, token, payload):
        calls["n"] += 1
        raise HTTPException(status_code=400, detail="闪剪错误 InvalidParam: bad")

    monkeypatch.setattr(sj, "_post", fake_post)
    with pytest.raises(HTTPException):
        asyncio.run(sj._post_virtualman_with_queue("token", {}))
    assert calls["n"] == 1