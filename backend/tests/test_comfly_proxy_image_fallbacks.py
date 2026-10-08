from decimal import Decimal

import pytest

from backend.app.api import comfly_proxy
from backend.app.api.comfly_proxy import (
    _body_for_upstream_model,
    _image_generation_channel_available,
    _image_generation_model_attempts,
    _image_generation_model_attempts_for_user,
    _mark_image_generation_channel_failure,
    _is_image_download_interrupted_payload,
)
from mcp.comfly_upstream import lookup_comfly_model


def test_gpt_image2_attempts_put_comfyui_official_after_default_comfly():
    assert _image_generation_model_attempts("gpt-image-2") == [
        "gpt-image-2-gaisc",
        "gpt-image-2",
        "gpt-image-2-comfyui-official",
        "gpt-image-2-sutui",
        "gpt-image-2-openmindapi",
        "nano-banana-2",
    ]


def test_gpt_image2_attempts_append_nano_banana_for_official_channel_users():
    assert _image_generation_model_attempts_for_user(
        "gpt-image-2",
        openai_official_first=True,
    ) == [
        "gpt-image-2-openai-official",
        "gpt-image-2-gaisc",
        "gpt-image-2",
        "gpt-image-2-comfyui-official",
        "gpt-image-2-sutui",
        "gpt-image-2-openmindapi",
        "nano-banana-2",
    ]


def test_gpt_image2_attempts_promote_openmind_when_it_is_enabled():
    assert _image_generation_model_attempts_for_user(
        "gpt-image-2",
        openai_official_first=False,
        prefer_openmind=True,
    ) == [
        "gpt-image-2-openmindapi",
        "gpt-image-2-gaisc",
        "gpt-image-2",
        "gpt-image-2-comfyui-official",
        "gpt-image-2-sutui",
        "nano-banana-2",
    ]


def test_gpt_image2_openmind_stays_after_official_channel_for_entitled_users():
    assert _image_generation_model_attempts_for_user(
        "gpt-image-2",
        openai_official_first=True,
        prefer_openmind=True,
    )[:3] == [
        "gpt-image-2-openai-official",
        "gpt-image-2-openmindapi",
        "gpt-image-2-gaisc",
    ]


def test_openai_gpt_image2_alias_uses_same_fallback_chain():
    assert _image_generation_model_attempts("openai/gpt-image-2") == [
        "gpt-image-2-gaisc",
        "gpt-image-2",
        "gpt-image-2-comfyui-official",
        "gpt-image-2-sutui",
        "gpt-image-2-openmindapi",
        "nano-banana-2",
    ]


def test_sutui_gpt_image2_pricing_entry_exists():
    entry = lookup_comfly_model("gpt-image-2-sutui")

    assert entry is not None
    assert entry["token_group"] == "sutui"
    assert entry["comfly_model"] == "openai/gpt-image-2"


def test_comfyui_official_gpt_image2_pricing_entry_exists():
    entry = lookup_comfly_model("gpt-image-2-comfyui-official")

    assert entry is not None
    assert entry["token_group"] == "comfyui_official"
    assert entry["comfly_model"] == "gpt-image-2"

def test_non_gpt_image2_models_keep_original_attempt_sequence():
    assert _image_generation_model_attempts("nano-banana-2") == ["nano-banana-2"]
    assert _image_generation_model_attempts_for_user(
        "nano-banana-2",
        openai_official_first=True,
    ) == ["nano-banana-2"]


def test_nano_banana_body_is_normalized_for_gpt_fallback_requests():
    entry = lookup_comfly_model("nano-banana-2") or {}
    body = _body_for_upstream_model(
        {
            "prompt": "clean product photo",
            "size": "9:16",
            "n": 2,
            "response_format": "url",
            "image_url": "https://example.com/ref.png",
        },
        "nano-banana-2",
        entry,
    )
    assert body["model"] == "nano-banana-2"
    assert body["image_size"] == "9:16"
    assert body["aspect_ratio"] == "9:16"
    assert body["num_images"] == 2
    assert body["n"] == 2
    assert body["image_url"] == "https://example.com/ref.png"
    assert body["image_urls"] == ["https://example.com/ref.png"]


def test_gaisc_portrait_size_is_aligned_to_its_sixteen_pixel_requirement():
    entry = lookup_comfly_model("gpt-image-2-gaisc") or {}
    body = _body_for_upstream_model(
        {"prompt": "portrait product", "size": "1080x1920", "n": 1},
        "gpt-image-2-gaisc",
        entry,
    )

    assert body["size"] == "1088x1920"


@pytest.mark.asyncio
async def test_openmind_candidate_uses_direct_request_without_reference_image(monkeypatch):
    captured = {}

    async def _fake_openmind_request(body):
        captured["body"] = body
        return {"data": [{"url": "https://example.com/generated.png"}]}

    monkeypatch.setattr(comfly_proxy, "_openai_official_image_first_for_user", lambda _user_id: False)
    monkeypatch.setattr(comfly_proxy, "_openmind_image_fallback_enabled", lambda: True)
    monkeypatch.setattr(comfly_proxy, "_image_generation_channel_available", lambda *_args: True)
    monkeypatch.setattr(comfly_proxy, "_openmind_image_request", _fake_openmind_request)
    monkeypatch.setattr(comfly_proxy, "_queue_generated_image_asset_persistence", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(comfly_proxy, "estimate_comfly_credits", lambda *_args, **_kwargs: 30)
    monkeypatch.setattr(comfly_proxy, "_do_pre_deduct_by_user_id", lambda *_args, **_kwargs: Decimal("30"))
    monkeypatch.setattr(comfly_proxy, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comfly_proxy, "log_model_usage_event", lambda *_args, **_kwargs: None)

    response = await comfly_proxy._execute_image_generation_request(
        request_user_id=48,
        billing_user_id=48,
        model="gpt-image-2",
        body={"prompt": "clean product photo", "size": "9:16", "n": 1},
    )

    assert captured["body"]["size"] == "9:16"
    assert response["_lobster_fallback"]["used_model"] == "gpt-image-2-openmindapi"


def test_image_download_timeout_payload_is_detected_for_video_image_fallback():
    assert _is_image_download_interrupted_payload(
        {
            "error": {
                "message": "Timed out while downloading image from ip:port",
                "type": "new_api_error",
                "code": "unknown_error",
            }
        }
    )


def test_image_generation_channel_failure_temporarily_skips_known_bad_provider(monkeypatch):
    cache: dict[str, str] = {}

    monkeypatch.setattr(
        "backend.app.api.comfly_proxy.cache_get",
        lambda key: cache.get(key),
    )
    monkeypatch.setattr(
        "backend.app.api.comfly_proxy.cache_set",
        lambda key, value="1", ttl_seconds=10.0: cache.__setitem__(key, value),
    )

    assert _image_generation_channel_available("openai_official", "gpt-image-2-openai-official")

    marked = _mark_image_generation_channel_failure(
        "openai_official",
        "gpt-image-2-openai-official",
        "OpenAI official HTTP 429: You have no credits remaining. code=insufficient_quota",
    )

    assert marked is True
    assert not _image_generation_channel_available("openai_official", "gpt-image-2-openai-official")
    assert _image_generation_channel_available("gaisc", "gpt-image-2-gaisc")
    assert _image_generation_channel_available("comfyui_official", "gpt-image-2-comfyui-official")


def test_image_generation_channel_failure_circuits_transient_upstream_error(monkeypatch):
    cache: dict[str, str] = {}

    monkeypatch.setattr("backend.app.api.comfly_proxy.cache_get", lambda key: cache.get(key))
    monkeypatch.setattr(
        "backend.app.api.comfly_proxy.cache_set",
        lambda key, value="1", ttl_seconds=10.0: cache.__setitem__(key, value),
    )

    marked = _mark_image_generation_channel_failure(
        "gaisc",
        "gpt-image-2-gaisc",
        "Comfly HTTP 503: upstream temporarily unavailable",
    )

    assert marked is True
    assert not _image_generation_channel_available("gaisc", "gpt-image-2-gaisc")


def test_openmind_api_channel_failure_also_skips_openmind_fallback(monkeypatch):
    cache: dict[str, str] = {}

    monkeypatch.setattr("backend.app.api.comfly_proxy.cache_get", lambda key: cache.get(key))
    monkeypatch.setattr(
        "backend.app.api.comfly_proxy.cache_set",
        lambda key, value="1", ttl_seconds=10.0: cache.__setitem__(key, value),
    )

    assert _mark_image_generation_channel_failure(
        "openmindapi",
        "gpt-image-2-openmindapi",
        "OpenMind HTTP 429: no credits remaining",
    )

    assert not _image_generation_channel_available("openmind", "gpt-image-2-openmindapi")
    assert not _image_generation_channel_available("openmindapi", "gpt-image-2")
