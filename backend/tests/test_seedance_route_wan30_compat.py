"""老客户端兼容：wan3.0 被打到 seedance 直连路由时改走 DashScope + 兜底提示词截断。

背景（2026-09-20 user 116 / 15683020857 等 7 个用户）：
  OTA 落后于 2026-09-12 的客户端不认识 dashscope 通道，会把 wan3.0-video 提交到
  /api/comfly-proxy/seedance/v3/contents/generations/tasks，Comfly 直接 404 →
  客户端看到 "Comfly Seedance submit 调用失败：Comfly HTTP 404"；
  同一批老客户端也没有客户端侧 3800 字符截断，兜底的 comfly/xai/openmind 三个 grok 通道
  全部回 "Prompt length exceeds the maximum allowed length of 4096"，整单失败。

覆盖：
1. seedance 风格 body → wan3.0 body 的字段抽取（content 里的 text/image、扁平字段）；
2. seedance 路由收到 wan3.0 时改走 DashScope，并把 task id 补到顶层 id/task_id（客户端只认这个）；
3. seedance 路由收到普通 seedance 模型时仍走原来的 Comfly 直连；
4. 提示词超长时按上游上限截断（保留头尾），不超长时原样返回。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import pytest

from backend.app.api import comfly_proxy


class _FakeRequest:
    """只满足路由里用到的接口：await json() / headers / client。"""

    def __init__(self, payload: Dict[str, Any], headers: Dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    async def json(self) -> Dict[str, Any]:
        return self._payload


# —— 1. body 抽取 ——

def test_wan30_body_extracts_prompt_and_first_frame_from_seedance_content():
    body = {
        "model": "wan3.0-video",
        "content": [
            {"type": "text", "text": "城市夜景空镜，镜头缓慢推进"},
            {"type": "image_url", "image_url": {"url": "https://cdn.example/first.png"}},
        ],
        "ratio": "9:16",
        "duration": 10,
        "resolution": "720P",
    }
    out = comfly_proxy._wan30_body_from_seedance_payload(body)
    assert out["model"] == "wan3.0-video"
    assert out["prompt"] == "城市夜景空镜，镜头缓慢推进"
    assert out["image_url"] == "https://cdn.example/first.png"
    assert out["images"] == ["https://cdn.example/first.png"]
    assert out["ratio"] == "9:16"
    assert out["duration"] == 10
    assert out["resolution"] == "720P"


def test_wan30_body_accepts_flat_fields_and_multiple_text_parts():
    body = {
        "model": "wan3.0",
        "prompt": "主提示词",
        "content": [{"type": "text", "text": "补充描述"}],
        "images": ["https://cdn.example/a.png"],
        "aspect_ratio": "16:9",
        "seconds": 5,
    }
    out = comfly_proxy._wan30_body_from_seedance_payload(body)
    assert out["prompt"] == "主提示词"
    assert out["image_url"] == "https://cdn.example/a.png"
    assert out["ratio"] == "16:9"
    assert out["duration"] == 5
    assert out["model"] == "wan3.0-video"  # 别名归一


# —— 2/3. seedance 路由分发 ——

@pytest.fixture
def seedance_route_env(monkeypatch, db_session_factory, patch_fuiou_settings):
    monkeypatch.setattr(comfly_proxy, "SessionLocal", db_session_factory)
    monkeypatch.setattr(comfly_proxy, "_check_request_authorized_for_billing", lambda request: None)
    monkeypatch.setattr(comfly_proxy, "_resolve_proxy_user_ids_from_request", lambda request, map_to_online_user=False: (116, 116))
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", lambda *args, **kwargs: comfly_proxy.Decimal("1200"))
    monkeypatch.setattr(comfly_proxy, "_do_full_refund_by_user_id", lambda *args, **kwargs: None)
    monkeypatch.setattr(comfly_proxy, "_comfly_url", lambda endpoint, model="": f"https://example.test{endpoint}")
    monkeypatch.setattr(comfly_proxy, "_comfly_headers", lambda model="": {"Authorization": "Bearer fake"})
    monkeypatch.setattr(comfly_proxy, "_comfly_auth_headers", lambda model="": {"Authorization": "Bearer fake"})
    return db_session_factory


def test_seedance_route_routes_wan30_to_dashscope(seedance_route_env, monkeypatch):
    seen: Dict[str, Any] = {}

    async def fake_wan_generate(model, body):
        seen["model"] = model
        seen["body"] = body
        return {"output": {"task_id": "ds-task-1", "task_status": "RUNNING"}, "request_id": "req-1"}

    monkeypatch.setattr(comfly_proxy, "call_comfly_video_generate", fake_wan_generate)

    async def boom(*args, **kwargs):
        raise AssertionError("wan3.0 不该再打到 Comfly 的 seedance 直连端点")

    monkeypatch.setattr(comfly_proxy, "_comfly_request", boom)

    request = _FakeRequest(
        {
            "model": "wan3.0-video",
            "content": [{"type": "text", "text": "城市空镜"}],
            "ratio": "9:16",
            "duration": 10,
        }
    )
    response = asyncio.run(comfly_proxy.proxy_seedance_tasks_submit(request))
    payload = json.loads(response.body)
    assert payload["id"] == "ds-task-1"
    assert payload["task_id"] == "ds-task-1"
    assert payload["output"]["task_status"] == "RUNNING"
    # 归一后的 body 里应该带上了从 content 抽出来的提示词与比例/时长
    assert seen["model"] == "wan3.0-video"
    assert seen["body"]["prompt"] == "城市空镜"
    assert seen["body"]["ratio"] == "9:16"
    assert seen["body"]["duration"] == 10


def test_seedance_route_keeps_comfly_path_for_seedance_models(seedance_route_env, monkeypatch):
    monkeypatch.setattr(
        comfly_proxy,
        "_require_model_entry",
        lambda model: {"api_format": "seedance_v3", "token_group": "comfly"},
    )
    seen: Dict[str, Any] = {}

    async def fake_comfly_request(method, url, body, headers, timeout):
        seen["url"] = url
        return {"id": "seed-task-9"}

    monkeypatch.setattr(comfly_proxy, "_comfly_request", fake_comfly_request)

    async def boom(*args, **kwargs):
        raise AssertionError("普通 seedance 模型不该走 wan 兼容路径")

    monkeypatch.setattr(comfly_proxy, "_submit_wan30_via_seedance_route", boom)
    monkeypatch.setattr(comfly_proxy, "_require_model_entry", lambda model: {"api_format": "seedance_v3", "token_group": "comfly"})

    request = _FakeRequest({"model": "doubao-seedance-2-0-fast-260128", "content": [{"type": "text", "text": "x"}]})
    response = asyncio.run(comfly_proxy.proxy_seedance_tasks_submit(request))
    assert json.loads(response.body)["id"] == "seed-task-9"
    assert "seedance/v3/contents/generations/tasks" in seen["url"]


# —— 4. 提示词截断 ——

def test_upstream_prompt_limit_keeps_short_text():
    assert comfly_proxy._limit_upstream_video_prompt("短提示词") == "短提示词"
    assert comfly_proxy._limit_upstream_video_prompt(None) == ""


def test_upstream_prompt_limit_truncates_long_text_head_and_tail():
    text = "头部内容" * 800 + "尾部内容" * 800  # 4800 字
    out = comfly_proxy._limit_upstream_video_prompt(text)
    assert len(out) <= 3900
    assert out.startswith("头部内容")
    assert out.endswith("尾部内容")
    assert "已截断" in out


def test_grok_family_bodies_apply_prompt_limit():
    long_prompt = "很长的提示词" * 1200  # 7200 字
    xai_body = comfly_proxy._xai_video_body({"prompt": long_prompt, "duration": 10}, "grok-imagine-video-1.5")
    assert len(xai_body["prompt"]) <= 3900
    openmind_body = comfly_proxy._openmind_video_body(
        {"prompt": long_prompt, "duration": 10}, "grok-video-3", {"api_format": "openmind"}
    )
    assert len(openmind_body["prompt"]) <= 3900
