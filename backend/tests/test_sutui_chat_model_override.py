from backend.app.api.sutui_chat_proxy import (
    _request_has_multimodal_images,
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

    assert attempts[0]["model"] == "apiz/seed-2.0-mini"
    assert attempts[0]["provider"] == "xskill"
    assert attempts[0]["is_direct"] is False
    assert any(a["model"] == "deepseek-chat" for a in attempts)


def test_default_deepseek_chat_keeps_existing_fallback_routes(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "deepseek_api_key", None, raising=False)
    candidates = _sutui_chat_model_candidates("deepseek-chat")
    attempts = _sutui_chat_attempts_for_models(
        candidates,
        "sutui-token",
        forced_model_override=False,
    )

    assert candidates == ["apiz/seed-2.0-mini", "deepseek-chat"]
    assert [(a["model"], a["provider"]) for a in attempts] == [
        ("apiz/seed-2.0-mini", "xskill"),
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

    assert candidates == ["apiz/seed-2.0-mini", "deepseek-chat"]
    assert (attempts[0]["model"], attempts[0]["provider"]) == (
        "apiz/seed-2.0-mini",
        "xskill",
    )


def test_disabled_sol_is_skipped_even_when_requested(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)
    candidates = _sutui_chat_model_candidates("openai/gpt-5.6-sol")

    assert candidates == ["apiz/seed-2.0-mini", "deepseek-chat"]


def test_user_preference_uses_terra_then_deepseek(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", None, raising=False)
    monkeypatch.setenv("SUTUI_CHAT_MODEL_FALLBACK_CHAIN_JSON", '["openai/gpt-5.6-sol"]')
    monkeypatch.delenv("SUTUI_CHAT_MODEL_MAP_JSON", raising=False)

    candidates = _sutui_chat_model_candidates(
        "openai/gpt-4.1",
        fallback_chain=["openai/gpt-5.6-terra", "deepseek-chat"],
    )

    assert candidates == [
        "apiz/seed-2.0-mini",
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

    assert candidates == ["apiz/seed-2.0-mini", "deepseek-chat"]


def test_configured_yyapi_is_first_but_keeps_direct_fallbacks(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", "test-yyapi-key")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_base", "https://www.yyapi.cloud")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_chat_model", "gpt-5.6-sol")

    candidates = _sutui_chat_model_candidates("deepseek-chat", has_tools=True)
    attempts = _sutui_chat_attempts_for_models(candidates, "sutui-token")

    assert candidates[:3] == ["gpt-5.6-sol", "apiz/seed-2.0-mini", "deepseek-chat"]
    assert [(a["model"], a["provider"], a["is_direct"]) for a in attempts[:2]] == [
        ("gpt-5.6-sol", "direct:yyapi", True),
        ("apiz/seed-2.0-mini", "xskill", False),
    ]


def test_yyapi_route_keeps_fallback_candidates(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", "test-yyapi-key")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_chat_model", "gpt-5.6-sol")
    monkeypatch.setattr(sutui_chat_proxy.settings, "deepseek_api_key", "test-deepseek-key")

    candidates = _sutui_chat_model_candidates("openai/gpt-4.1")
    attempts = _sutui_chat_attempts_for_models(candidates, "sutui-token")

    assert ("gpt-5.6-sol", "direct:yyapi") in [(a["model"], a["provider"]) for a in attempts]
    assert ("deepseek-chat", "direct:deepseek") in [(a["model"], a["provider"]) for a in attempts]


def test_image_candidates_put_seed_after_yyapi_and_never_use_deepseek(monkeypatch):
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_api_key", "test-yyapi-key")
    monkeypatch.setattr(sutui_chat_proxy.settings, "yyapi_chat_model", "gpt-5.6-sol")
    monkeypatch.delenv("SUTUI_CHAT_DISABLED_MODELS_JSON", raising=False)

    candidates = _sutui_chat_model_candidates("deepseek-chat", has_images=True)
    assert candidates == ["gpt-5.6-sol", "apiz/seed-2.0-mini"]

    attempts = _sutui_chat_attempts_for_models(candidates, "sutui-token")
    assert [(a["model"], a["provider"]) for a in attempts] == [
        ("gpt-5.6-sol", "direct:yyapi"),
        ("apiz/seed-2.0-mini", "xskill"),
    ]


def test_request_image_detection_handles_remote_and_data_uri_parts():
    assert _request_has_multimodal_images({
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ]}],
    })
    assert _request_has_multimodal_images({
        "messages": [{"role": "user", "content": [
            {"type": "image", "image": "data:image/png;base64,AAAA"},
        ]}],
    })
    assert not _request_has_multimodal_images({
        "messages": [{"role": "user", "content": "plain text"}],
    })
