from __future__ import annotations


def test_gpt_55_is_registered_for_comfly_chat_billing(monkeypatch):
    from mcp.comfly_upstream import estimate_comfly_credits, lookup_comfly_model

    monkeypatch.setenv("USER_PRICE_MULTIPLIER", "2")

    entry = lookup_comfly_model("gpt-5.5")

    assert entry is not None
    assert entry["api_format"] == "chat"
    assert entry["price_type"] == "per_token"
    assert entry["comfly_model"] == "gpt-5.5"
    assert estimate_comfly_credits(
        "gpt-5.5",
        {"usage": {"prompt_tokens": 4692, "completion_tokens": 9}},
        for_user=True,
    ) == 1
