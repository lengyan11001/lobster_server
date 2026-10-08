"""直连 DeepSeek 可用时，不再叠加 xskill 的 deepseek 中转通道。

背景（2026-09-15 diag_20260915034723_cc2337ca）：数字人口播视频节点生成文案时，
候选链在 deepseek-chat 这一跳之后还挂了
  ('deepseek/deepseek-v3.2','xskill-v3') 和 ('deepseek-chat','xskill')
这两条中转，那晚它们一直报 "No available fal accounts"（503），白耗时间。
现在只要该 model id 有官方直连，就直接进入下一个候选。
"""
import backend.app.api.sutui_chat_proxy as sutui_chat_proxy
from backend.app.api.sutui_chat_proxy import (
    _sutui_chat_attempts_for_models,
    _sutui_chat_model_candidates,
)


def _pairs(attempts):
    return [(item["model"], item["provider"]) for item in attempts]


def test_direct_deepseek_route_drops_the_xskill_deepseek_hops(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "change2pro_api_key", None, raising=False)
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.setattr(sutui_chat_proxy.settings, "deepseek_api_key", "test-deepseek-key")
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)

    candidates = _sutui_chat_model_candidates("deepseek-chat")
    pairs = _pairs(_sutui_chat_attempts_for_models(candidates, "sutui-token"))

    # 两个直连档都保留
    assert ("deepseek-flash", "direct:deepseek") in pairs
    assert ("deepseek-chat", "direct:deepseek") in pairs
    # 这两跳被去掉
    assert ("deepseek/deepseek-v3.2", "xskill-v3") not in pairs
    assert ("deepseek-chat", "xskill") not in pairs
    # 没有直连的候选仍然走 xskill
    assert any(provider == "xskill" for _model, provider in pairs)


def test_without_a_deepseek_key_the_deepseek_candidates_are_dropped(monkeypatch):
    """没有官方直连 key 时，DeepSeek 的候选直接跳过，不再退回 xskill。"""
    monkeypatch.setattr(sutui_chat_proxy.settings, "change2pro_api_key", "test-change2pro-key", raising=False)
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", "test-yyapi-key", raising=False)
    monkeypatch.setattr(sutui_chat_proxy.settings, "deepseek_api_key", None, raising=False)
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)

    candidates = _sutui_chat_model_candidates("deepseek-chat")
    pairs = _pairs(_sutui_chat_attempts_for_models(candidates, "sutui-token"))

    assert ("deepseek-flash", "direct:deepseek") not in pairs
    assert ("deepseek-chat", "direct:deepseek") not in pairs
    assert ("deepseek/deepseek-v3.2", "xskill-v3") not in pairs
    assert ("deepseek-chat", "xskill") not in pairs
    assert pairs == [
        ("gpt-5.6-sol", "direct:change2pro"),
        ("gpt-5.6-sol", "direct:yyapi"),
        ("apiz/seed-2.0-mini", "xskill"),
    ]
