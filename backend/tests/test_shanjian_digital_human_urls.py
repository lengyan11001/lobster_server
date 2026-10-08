from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.shanjian_digital_human import _resolve_asset_or_url


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/shanjian-digital-human/profile/train",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/assets/file/photo",
        "https://localhost:8000/api/assets/file/photo",
        "http://192.168.1.20:8000/api/assets/file/photo",
        "http://10.0.0.8:8000/api/assets/file/photo",
    ],
)
def test_digital_human_training_rejects_local_and_private_urls(url):
    with pytest.raises(HTTPException) as exc_info:
        _resolve_asset_or_url(
            request=_request(),
            db=None,
            current_user=SimpleNamespace(id=1),
            url=url,
            asset_id=None,
            label="training image",
        )

    assert exc_info.value.status_code == 400
    assert "public address" in str(exc_info.value.detail)


def test_digital_human_training_accepts_public_url():
    url = "https://cdn.example.com/training-image.png"

    assert (
        _resolve_asset_or_url(
            request=_request(),
            db=None,
            current_user=SimpleNamespace(id=1),
            url=url,
            asset_id=None,
            label="training image",
        )
        == url
    )
