from backend.app.api.sutui_chat_proxy import (
    _sutui_chat_attempts_for_models,
    _sutui_chat_model_candidates,
)
import backend.app.api.sutui_chat_proxy as sutui_chat_proxy


def test_model_override_still_keeps_deepseek_fallback_route(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.setattr(sutui_chat_proxy.settings, "deepseek_api_key", None, raising=False)
    attempts = _sutui_chat_attempts_for_models(
        _sutui_chat_model_candidates("openai/gpt-4.1"),
        "sutui-token",
        forced_model_override=False,
    )

    assert attempts[0]["model"] == "deepseek/deepseek-v3.2"
    assert attempts[0]["provider"] == "xskill-v3"
    assert attempts[0]["is_direct"] is False
    assert any(a["model"] == "deepseek-chat" for a in attempts)


def test_default_deepseek_chat_keeps_existing_fallback_routes():
    attempts = _sutui_chat_attempts_for_models(
        ["deepseek-chat"],
        "sutui-token",
        forced_model_override=False,
    )

    assert [(a["model"], a["provider"]) for a in attempts] == [
        ("deepseek-chat", "direct:deepseek"),
        ("deepseek/deepseek-v3.2", "xskill-v3"),
        ("deepseek-chat", "xskill"),
    ]


def test_default_chain_uses_provider_qualified_gpt_5_6_as_final_fallback(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.delenv("SUTUI_CHAT_MODEL_FALLBACK_CHAIN_JSON", raising=False)
    monkeypatch.delenv("SUTUI_CHAT_MODEL_MAP_JSON", raising=False)
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)

    candidates = _sutui_chat_model_candidates("deepseek-chat")
    attempts = _sutui_chat_attempts_for_models(
        candidates,
        "sutui-token",
        forced_model_override=False,
    )

    assert candidates == ["deepseek-chat", "openai/gpt-5.6-terra"]
    assert (attempts[-1]["model"], attempts[-1]["provider"]) == (
        "openai/gpt-5.6-terra",
        "xskill",
    )


def test_disabled_sol_is_skipped_even_when_requested(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)
    candidates = _sutui_chat_model_candidates("openai/gpt-5.6-sol")

    assert candidates == [
        "deepseek-chat",
        "openai/gpt-5.6-terra",
    ]


def test_user_preference_uses_terra_then_deepseek(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.setenv("SUTUI_CHAT_MODEL_FALLBACK_CHAIN_JSON", '["openai/gpt-5.6-sol"]')
    monkeypatch.delenv("SUTUI_CHAT_MODEL_MAP_JSON", raising=False)

    candidates = _sutui_chat_model_candidates(
        "openai/gpt-4.1",
        fallback_chain=["openai/gpt-5.6-terra", "deepseek-chat"],
    )

    assert candidates == [
        "deepseek-chat",
        "openai/gpt-4.1",
        "openai/gpt-5.6-terra",
    ]


def test_mastra_requires_deepseek_even_when_global_chain_omits_it(monkeypatch):
    monkeypatch.setenv("SUTUI_CHAT_MODEL_FALLBACK_CHAIN_JSON", '["openai/gpt-5.6-sol"]')
    monkeypatch.delenv("SUTUI_CHAT_MODEL_MAP_JSON", raising=False)
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)

    candidates = _sutui_chat_model_candidates(
        "openai/gpt-5.6-sol",
        has_tools=True,
        required_fallbacks=["deepseek-chat"],
    )

    assert candidates == ["deepseek-chat"]


def test_configured_yyapi_is_first_but_keeps_direct_fallbacks(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", "test-yyapi-key")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_base", "https://www.yyapi.cloud")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_chat_model", "gpt-5.6-sol")

    candidates = _sutui_chat_model_candidates("deepseek-chat", has_tools=True)
    attempts = _sutui_chat_attempts_for_models(candidates, "sutui-token")

    assert candidates[:2] == ["gpt-5.6-sol", "deepseek-chat"]
    assert [(a["model"], a["provider"], a["is_direct"]) for a in attempts[:2]] == [
        ("gpt-5.6-sol", "direct:yyapi", True),
        ("deepseek-chat", "direct:deepseek", True),
    ]


def test_yyapi_route_keeps_fallback_candidates(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", "test-yyapi-key")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_chat_model", "gpt-5.6-sol")
    monkeypatch.setattr(sutui_chat_proxy.settings, "deepseek_api_key", "test-deepseek-key")

    candidates = _sutui_chat_model_candidates("openai/gpt-4.1")
    attempts = _sutui_chat_attempts_for_models(candidates, "sutui-token")

    assert ("gpt-5.6-sol", "direct:yyapi") in [(a["model"], a["provider"]) for a in attempts]
    assert ("deepseek-chat", "direct:deepseek") in [(a["model"], a["provider"]) for a in attempts]
