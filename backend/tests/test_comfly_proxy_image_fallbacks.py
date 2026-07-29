from backend.app.api.comfly_proxy import (
    _body_for_upstream_model,
    _image_generation_model_attempts,
    _image_generation_model_attempts_for_user,
)
from mcp.comfly_upstream import lookup_comfly_model


def test_gpt_image2_attempts_append_nano_banana_for_regular_users():
    assert _image_generation_model_attempts("gpt-image-2") == [
        "gpt-image-2",
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
