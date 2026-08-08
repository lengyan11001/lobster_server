from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


def _request(*, brand_header: str = "", query: bytes = b"") -> Request:
    headers = []
    if brand_header:
        headers.append((b"x-lobster-brand", brand_header.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "query_string": query,
            "headers": headers,
        }
    )


def test_request_brand_context_uses_one_consistent_signal():
    from backend.app.services.brand_context import (
        explicit_request_brand_mark,
        resolve_request_brand_mark,
    )

    request = _request(brand_header="daka", query=b"brand=daka&brand_mark=daka")

    assert explicit_request_brand_mark(request) == "daka"
    assert resolve_request_brand_mark(request, "daka") == "daka"
    assert resolve_request_brand_mark(_request()) == "bihuo"


@pytest.mark.parametrize(
    ("brand_request", "body_brand"),
    [
        (_request(brand_header="daka", query=b"brand=bihuo"), None),
        (_request(brand_header="daka"), "bihuo"),
        (_request(query=b"brand=daka&brand_mark=bihuo"), None),
        (_request(query=b"brand=daka&brand=bihuo"), None),
    ],
)
def test_request_brand_context_rejects_conflicting_signals(brand_request: Request, body_brand: str | None):
    from backend.app.services.brand_context import resolve_request_brand_mark

    with pytest.raises(HTTPException) as error:
        resolve_request_brand_mark(brand_request, body_brand)

    assert error.value.status_code == 400
    assert "品牌参数不一致" in str(error.value.detail)


def test_public_entry_points_reject_conflicting_brand_sources(db_session_factory, monkeypatch):
    from backend.app.api.auth import router as auth_router
    from backend.app.api.branding import router as branding_router
    from backend.app.api.mobile_client import router as mobile_router
    from backend.app.core.config import settings
    from backend.app.db import get_db

    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(branding_router)
    app.include_router(mobile_router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    client = TestClient(app)

    login = client.post(
        "/auth/login-phone-password?brand=daka",
        headers={"X-Lobster-Brand": "daka"},
        json={"phone": "13800138000", "password": "password", "brand_mark": "bihuo"},
    )
    branding = client.get(
        "/api/branding?brand=bihuo",
        headers={"X-Lobster-Brand": "daka"},
    )
    mobile = client.get(
        "/api/mobile/phone/status?phone=13800138000&brand_mark=bihuo",
        headers={"X-Lobster-Brand": "daka"},
    )

    assert login.status_code == 400
    assert branding.status_code == 400
    assert mobile.status_code == 400


def test_h5_network_transports_use_the_shared_brand_context():
    root = Path(__file__).resolve().parents[2]
    script = (root / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    fetch_lines = [line.strip() for line in script.splitlines() if "fetch(" in line]

    assert fetch_lines
    assert all("fetch(apiUrl(" in line for line in fetch_lines)
    assert '"X-Lobster-Brand": H5_BRAND_MARK' in script
    assert 'url.searchParams.set("brand", H5_BRAND_MARK)' in script
    assert 'params.set("brand", H5_BRAND_MARK)' in script


def test_miniprogram_network_calls_are_confined_to_brand_aware_wrapper():
    root = Path(__file__).resolve().parents[2] / "miniprogram"
    direct_call_files = []
    for path in root.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if "wx.request(" in text or "wx.uploadFile(" in text:
            direct_call_files.append(path.relative_to(root).as_posix())

    assert direct_call_files == ["utils/api.js"]
    wrapper = (root / "utils" / "api.js").read_text(encoding="utf-8")
    assert '"X-Lobster-Brand": config.BRAND_MARK' in wrapper
    assert "brand_mark: config.BRAND_MARK" in wrapper
    assert "brand=${encodeURIComponent(config.BRAND_MARK)}" in wrapper


def test_admin_requests_use_server_rendered_brand_and_shared_headers():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "admin.html").read_text(encoding="utf-8")

    assert "document.documentElement.dataset.brand" in html
    assert "new URLSearchParams(location.search)" not in html
    assert "headers['X-Lobster-Brand'] = BRAND_MARK" in html
    assert "body: JSON.stringify({phone, captcha_id: loginCaptchaId, captcha_answer: captchaAnswer, brand_mark: BRAND_MARK})" in html
