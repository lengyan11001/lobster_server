from backend.app.api.comfly_proxy import (
    _body_for_upstream_model,
    _image_generation_channel_available,
    _image_generation_model_attempts,
    _image_generation_model_attempts_for_user,
    _mark_image_generation_channel_failure,
    _is_image_download_interrupted_payload,
)
from mcp.comfly_upstream import lookup_comfly_model


def test_gpt_image2_attempts_append_nano_banana_for_regular_users():
    assert _image_generation_model_attempts("gpt-image-2") == [
        "gpt-image-2",
        "gpt-image-2-gaisc",
        "gpt-image-2-openmindapi",
        "gpt-image-2-yunwu",
        "nano-banana-2",
    ]


def test_gpt_image2_attempts_append_nano_banana_for_official_channel_users():
    assert _image_generation_model_attempts_for_user(
        "gpt-image-2",
        openai_official_first=True,
    ) == [
        "gpt-image-2-openai-official",
        "gpt-image-2-gaisc",
        "gpt-image-2-openmindapi",
        "gpt-image-2-yunwu",
        "nano-banana-2",
    ]


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


def test_image_generation_channel_failure_ignores_ordinary_retryable_error(monkeypatch):
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

    assert marked is False
    assert _image_generation_channel_available("gaisc", "gpt-image-2-gaisc")


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
