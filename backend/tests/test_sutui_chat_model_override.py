from backend.app.api.sutui_chat_proxy import (
    _sutui_chat_attempts_for_models,
    _sutui_chat_model_candidates,
)


def test_model_override_still_keeps_deepseek_fallback_route():
    attempts = _sutui_chat_attempts_for_models(
        _sutui_chat_model_candidates("openai/gpt-4.1"),
        "sutui-token",
        forced_model_override=False,
    )

    assert attempts[0]["model"] == "openai/gpt-4.1"
    assert attempts[0]["provider"] == "xskill"
    assert attempts[0]["is_direct"] is False
    assert ("deepseek-chat", "direct:deepseek") in [(a["model"], a["provider"]) for a in attempts]


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
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)
    candidates = _sutui_chat_model_candidates("openai/gpt-5.6-sol")

    assert candidates == [
        "openai/gpt-5.6-terra",
        "deepseek-chat",
    ]


def test_user_preference_uses_terra_then_deepseek(monkeypatch):
    monkeypatch.setenv("SUTUI_CHAT_MODEL_FALLBACK_CHAIN_JSON", '["openai/gpt-5.6-sol"]')
    monkeypatch.delenv("SUTUI_CHAT_MODEL_MAP_JSON", raising=False)

    candidates = _sutui_chat_model_candidates(
        "openai/gpt-4.1",
        fallback_chain=["openai/gpt-5.6-terra", "deepseek-chat"],
    )

    assert candidates == [
        "openai/gpt-4.1",
        "openai/gpt-5.6-terra",
        "deepseek-chat",
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
