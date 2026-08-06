from __future__ import annotations

import plistlib

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(db_session_factory) -> TestClient:
    from backend.app.api.h5_chat import router
    from backend.app.db import get_db

    app = FastAPI()
    app.include_router(router)

    def _get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def test_daka_ios_webclip_uses_brand_url_name_and_icon(db_session_factory):
    from backend.app.api.h5_chat import _H5_STATIC_DIR

    response = _client(db_session_factory).get("/install/ios-webclip.mobileconfig?brand=daka")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="daka-ai-webclip.mobileconfig"'
    profile = plistlib.loads(response.content)
    clip = profile["PayloadContent"][0]
    assert clip["Label"] == "大咖AI员工"
    assert clip["URL"] == "https://h5.bhzn.top/?brand=daka"
    assert clip["PayloadIdentifier"] == "top.bhzn.h5.webclip.daka.clip"
    assert clip["Icon"] == (_H5_STATIC_DIR / "daka_256.png").read_bytes()
    assert profile["PayloadDisplayName"] == "大咖AI员工桌面入口"
    assert profile["PayloadIdentifier"] == "top.bhzn.h5.webclip.daka"


def test_ios_webclip_defaults_to_bihuo_without_brand(db_session_factory):
    response = _client(db_session_factory).get("/install/ios-webclip.mobileconfig")

    assert response.status_code == 200
    profile = plistlib.loads(response.content)
    clip = profile["PayloadContent"][0]
    assert clip["Label"] == "必火AI员工"
    assert clip["URL"] == "https://h5.bhzn.top/?brand=bihuo"
    assert clip["PayloadIdentifier"] == "top.bhzn.h5.webclip.bihuo.clip"


def test_hikong_ios_webclip_uses_server_oem_icon(db_session_factory):
    from backend.app.api.h5_chat import _ROOT

    response = _client(db_session_factory).get("/install/ios-webclip.mobileconfig?brand=hikong")

    assert response.status_code == 200
    profile = plistlib.loads(response.content)
    clip = profile["PayloadContent"][0]
    assert clip["Label"] == "海康AI智能体"
    assert clip["URL"] == "https://h5.bhzn.top/?brand=hikong"
    assert clip["Icon"] == (_ROOT / "client_static" / "oem" / "hikong" / "icon_256.png").read_bytes()


def test_h5_ios_download_passes_current_brand():
    from backend.app.api.h5_chat import _H5_STATIC_DIR

    script = (_H5_STATIC_DIR / "h5-app.js").read_text(encoding="utf-8")
    assert 'window.location.href = apiUrl("/install/ios-webclip.mobileconfig")' in script
