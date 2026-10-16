"""空正文（HTTP 200 + 无正文 + 无 tool_calls）不再被当成功接受。

2026-10-16 线上：用户 19168980789 发图 → mastra 图片理解拿到空正文 → 整轮失败。
根因之一：推理类模型把 max_tokens 花光时上游返回 content=""，但代理把这当"可用响应"
直接接受。本文件锁死新行为：换下一个候选 + 给下一跳更大输出预算。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api import sutui_chat_proxy as proxy  # noqa: E402
from backend.app.api.auth import get_current_user  # noqa: E402
from backend.app.db import get_db  # noqa: E402


def test_blank_completion_detection():
    blank = {"choices": [{"message": {"content": "", "reasoning_content": "想了很久"}}]}
    empty_ws = {"choices": [{"message": {"content": "   \n "}}]}
    ok = {"choices": [{"message": {"content": "这是一张海报"}}]}
    tool = {"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1"}]}}]}

    assert proxy._openai_completion_is_blank(blank) is True
    assert proxy._openai_completion_is_blank(empty_ws) is True
    assert proxy._openai_completion_is_blank(ok) is False
    assert proxy._openai_completion_is_blank(tool) is False, "有 tool_calls 不算空正文"
    assert proxy._openai_completion_visible_text(blank) == ""
    assert proxy._openai_completion_visible_text(ok) == "这是一张海报"


@pytest.fixture
def chat_client(db_session_factory, test_user):
    from backend.app.api.sutui_chat_proxy import router as chat_router

    app = FastAPI()
    app.include_router(chat_router, prefix="")

    def _db():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def _user():
        session = db_session_factory()
        try:
            from backend.app.models import User

            return session.query(User).filter(User.id == test_user.id).first()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _attempts():
    return [
        {"model": "model-blank", "provider": "p1", "api_base": "http://upstream.test", "endpoint_prefix": "/v1",
         "api_key": "k1", "is_direct": True, "timeout": 30},
        {"model": "model-good", "provider": "p2", "api_base": "http://upstream.test", "endpoint_prefix": "/v1",
         "api_key": "k2", "is_direct": True, "timeout": 30},
    ]


def _wire(monkeypatch, upstream):
    monkeypatch.setattr(proxy, "_sutui_chat_attempts_for_models", lambda *a, **k: _attempts())
    monkeypatch.setattr(proxy, "_require_balance_before_upstream_chat", lambda *a, **k: None)
    monkeypatch.setattr(proxy, "_should_deduct_credits", lambda: False)
    monkeypatch.setattr(proxy, "_release_db_before_upstream", lambda *a, **k: None)

    async def _token(*a, **k):
        return "sutui-token", "pool"

    async def _images(*a, **k):
        return None

    monkeypatch.setattr(proxy, "next_sutui_server_token_with_pool", _token)
    monkeypatch.setattr(proxy, "_prepare_multimodal_images", _images)
    monkeypatch.setattr(proxy, "_post_chat_upstream", upstream)


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._payload


def test_blank_first_candidate_falls_through_to_next(chat_client, monkeypatch):
    seen = []

    async def upstream(_client, _url, *, body, headers, timeout=None):
        seen.append(dict(body))
        if len(seen) == 1:
            return _Resp({"model": "model-blank", "choices": [
                {"message": {"content": "", "reasoning_content": "思考占满了预算"}, "finish_reason": "length"}]})
        return _Resp({"model": "model-good", "choices": [
            {"message": {"content": "图片里是一张税务局的宣传海报"}, "finish_reason": "stop"}]})

    _wire(monkeypatch, upstream)
    resp = chat_client.post(
        "/api/sutui-chat/completions",
        json={"model": "gpt-5.6-sol", "stream": False, "max_tokens": 2048,
              "messages": [{"role": "user", "content": "看图"}]},
        headers={"X-Lobster-Image-Understand": "1"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["choices"][0]["message"]["content"] == "图片里是一张税务局的宣传海报"
    assert len(seen) == 2, "空正文后应该换下一个候选"
    assert int(seen[1]["max_tokens"]) >= proxy._BLANK_TEXT_RETRY_MAX_TOKENS, "下一跳要拿到更大输出预算"


def test_blank_on_every_candidate_is_still_returned(chat_client, monkeypatch):
    seen = []

    async def upstream(_client, _url, *, body, headers, timeout=None):
        seen.append(dict(body))
        return _Resp({"choices": [{"message": {"content": "", "reasoning_content": "只剩推理"}}]})

    _wire(monkeypatch, upstream)
    resp = chat_client.post(
        "/api/sutui-chat/completions",
        json={"model": "gpt-5.6-sol", "stream": False, "messages": [{"role": "user", "content": "看图"}]},
        headers={"X-Lobster-Image-Understand": "1"},
    )
    assert resp.status_code == 200
    assert len(seen) == 2, "每个候选都要试过，但不会因此变成硬错误"
    assert resp.json()["choices"][0]["message"]["content"] == ""
