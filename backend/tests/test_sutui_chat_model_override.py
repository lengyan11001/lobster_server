from backend.app.api.sutui_chat_proxy import _sutui_chat_attempts_for_models


def test_forced_model_override_uses_single_exact_xskill_route():
    attempts = _sutui_chat_attempts_for_models(
        ["openai/gpt-4.1"],
        "sutui-token",
        forced_model_override=True,
    )

    assert len(attempts) == 1
    assert attempts[0]["model"] == "openai/gpt-4.1"
    assert attempts[0]["provider"] == "xskill-forced"
    assert attempts[0]["is_direct"] is False
    assert attempts[0]["forced_model"] is True


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
