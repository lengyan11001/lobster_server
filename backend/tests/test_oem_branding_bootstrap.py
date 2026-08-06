from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from backend.app.api.branding import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_oem_code_0400_resolves_hikong_manifest():
    response = _client().get("/api/oem/bootstrap?code=0400")

    assert response.status_code == 200
    payload = response.json()
    assert payload["oem_code"] == "0400"
    assert payload["brand_mark"] == "hikong"
    assert payload["profile"]["display_name"] == "海康AI智能体"
    assert payload["profile"]["install"]["launcher_filename"] == "海康AI智能体.exe"
    assert any(item["key"] == "launcher_exe" for item in payload["assets"])


@pytest.mark.parametrize(
    ("code", "mark", "display_name", "launcher_filename"),
    [
        ("0100", "bihuo", "必火AI员工", "必火智能AI.exe"),
        ("0200", "daka", "大咖AI员工", "大咖AI员工.exe"),
        ("0400", "hikong", "海康AI智能体", "海康AI智能体.exe"),
    ],
)
def test_enabled_factory_codes_have_complete_brand_launchers(code, mark, display_name, launcher_filename):
    response = _client().get(f"/api/oem/bootstrap?code={code}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["brand_mark"] == mark
    assert payload["profile"]["display_name"] == display_name
    assert payload["profile"]["install"]["launcher_filename"] == launcher_filename
    assert any(item["key"] == "launcher_exe" for item in payload["assets"])
    assert payload["assets"]


def test_oem_manifest_registers_brand_without_a_second_code_map():
    from backend.app.services.brand_context import BUILTIN_BRANDS

    assert BUILTIN_BRANDS["hikong"]["display_name"] == "海康AI智能体"
    assert BUILTIN_BRANDS["hikong"]["icon_32"] == "/client/oem/hikong/icon_32.png"


def test_oem_manifest_assets_exist_and_match_checksums():
    root = Path(__file__).resolve().parents[2]
    oem_root = root / "client_static" / "oem"
    manifest = json.loads((oem_root / "manifest.json").read_text(encoding="utf-8"))
    for brand in manifest["brands"].values():
        for item in brand["assets"]:
            path = root / "client_static" / item["url"].removeprefix("/client/")
            assert path.is_file(), item["url"]
            assert path.stat().st_size == item["size"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_h5_app_serves_oem_brand_assets_on_the_h5_origin():
    from backend.app.h5_main import app

    response = TestClient(app).get("/client/oem/daka/icon_32.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == (
        Path(__file__).resolve().parents[2] / "client_static" / "oem" / "daka" / "icon_32.png"
    ).read_bytes()


def test_unknown_oem_code_is_rejected():
    response = _client().get("/api/oem/bootstrap?code=9999")

    assert response.status_code == 404


def test_reserved_jinghai_code_waits_for_brand_assets():
    response = _client().get("/api/oem/bootstrap?code=0300")

    assert response.status_code == 409
    assert response.json()["detail"] == "鲸海AI员工资源尚未配置，请提供 Logo 后再安装"

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "client_static" / "oem" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["codes"]["0300"] == "jinghai"
    assert manifest["pending_brands"]["jinghai"] == {
        "display_name": "鲸海AI员工",
        "status": "pending_assets",
    }
    assert "jinghai" not in manifest["brands"]
