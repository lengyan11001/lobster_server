"""每跳必须有真正的墙钟上限，且逐跳 timeout 要真的传给 httpx。

背景（2026-09-15 diag_20260915034723_cc2337ca）：数字人口播视频节点的前置文案，
第一跳 deepseek-flash(官方直连) 声明 180s，实际等了 900s（DeepSeek 自己的排队上限），
第二跳 deepseek-chat 又等 900s —— 因为声明值只是 httpx 的“空闲读超时”，
而且 client 对象按 provider 缓存，只有第一次创建时的 timeout 生效。
"""
import asyncio

import httpx
import pytest

from backend.app.api import sutui_chat_proxy as proxy


class _FakeResponse:
    status_code = 200
    content = b"{}"


class _RecordingClient:
    def __init__(self, hang: bool = False) -> None:
        self.calls = []
        self.hang = hang

    async def post(self, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "timeout": timeout})
        if self.hang:
            # 模拟“上游排队但连接一直活着”：httpx 的空闲读超时永远不会触发
            await asyncio.sleep(60)
        return _FakeResponse()


@pytest.mark.asyncio
async def test_hop_timeout_is_forwarded_to_httpx_per_request():
    client = _RecordingClient()

    await proxy._post_chat_upstream(
        client,
        "https://example.invalid/v1/chat/completions",
        body={"model": "deepseek-flash"},
        headers={},
        timeout=7.5,
    )

    sent = client.calls[0]["timeout"]
    assert isinstance(sent, httpx.Timeout)
    assert sent.read == 7.5


@pytest.mark.asyncio
async def test_a_hanging_hop_is_cut_by_the_wall_clock():
    client = _RecordingClient(hang=True)
    started = asyncio.get_running_loop().time()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            proxy._post_chat_upstream(
                client,
                "https://example.invalid/v1/chat/completions",
                body={},
                headers={},
                timeout=30.0,
            ),
            timeout=0.2,
        )

    assert asyncio.get_running_loop().time() - started < 5


def test_chain_budget_covers_two_direct_hops_plus_a_winner(monkeypatch):
    """预算阶梯：两跳直连各 420s + 一跳 gpt-5.6-sol，链预算必须能覆盖。"""
    assert proxy._CHAT_DIRECT_ATTEMPT_TIMEOUT_SECONDS == 420.0
    assert proxy._CHAT_XSKILL_ATTEMPT_TIMEOUT_SECONDS == 240.0
    assert proxy._CHAT_CHAIN_BUDGET_SECONDS >= (
        2 * proxy._CHAT_DIRECT_ATTEMPT_TIMEOUT_SECONDS + 240.0
    )
