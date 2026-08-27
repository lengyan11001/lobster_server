from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.homepage import _HIKONG_HOME_HTML, _HOME_HTML, router


def test_public_homepage_shows_the_required_icp_record_link():
    assert "必火AI员工" in _HOME_HTML
    assert "粤ICP备2026043577号" in _HOME_HTML
    assert 'href="https://beian.miit.gov.cn/"' in _HOME_HTML
    assert 'target="_blank"' in _HOME_HTML


def test_hikong_domain_serves_its_own_oem_homepage():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/", headers={"host": "hikongai.cn"})

    assert response.status_code == 200
    assert "海康AI智能体 - 企业智能化执行平台" in response.text
    assert "/client/oem/hikong/logo_1024.png" in response.text
    assert "必火AI员工" not in response.text
    assert response.text == _HIKONG_HOME_HTML


def test_hikong_www_domain_serves_the_same_oem_homepage():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/", headers={"host": "www.hikongai.cn"})

    assert response.status_code == 200
    assert response.text == _HIKONG_HOME_HTML
