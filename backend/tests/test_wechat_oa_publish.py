from datetime import datetime

import pytest


def test_build_content_html_escapes_and_inserts_images():
    from backend.app.api.wechat_oa_publish import _build_content_html

    html = _build_content_html("第一段\n\n第二段 <b>", ["https://wx.example.test/a.png"])
    assert "<p>第一段</p>" in html
    assert "&lt;b&gt;" in html
    assert '<img src="https://wx.example.test/a.png"' in html


def test_wechat_error_ip_whitelist_mentions_server_ips(monkeypatch):
    from backend.app.api import wechat_oa_publish

    monkeypatch.setenv("WECHAT_OA_SERVER_IPS", "1.2.3.4,5.6.7.8")
    msg = wechat_oa_publish._wechat_error(
        {"errcode": 40164, "errmsg": "invalid ip 9.9.9.9, not in whitelist"}
    )
    assert "1.2.3.4" in msg
    assert "5.6.7.8" in msg
    assert "IP白名单" in msg


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"
        self.status_code = 200

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, get_payload=None, post_payload=None):
        self.get_payload = get_payload or {}
        self.post_payload = post_payload or {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, **kwargs):
        return _FakeResponse(self.get_payload)

    def post(self, url, params=None, json=None, files=None, **kwargs):
        self.calls.append({"url": url, "params": params, "json": json})
        return _FakeResponse(self.post_payload)


def test_save_config_encrypts_secret(db_session, test_user, monkeypatch):
    from backend.app.api import wechat_oa_publish
    from backend.app.models import UserWechatOfficialAccount

    monkeypatch.setattr(wechat_oa_publish, "_get_access_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(
        wechat_oa_publish.httpx,
        "Client",
        lambda **kwargs: _FakeClient(get_payload={"nickname": "测试公众号"}),
    )
    result = wechat_oa_publish.save_wechat_oa_config(
        wechat_oa_publish.WechatOaConfigBody(app_id="wx1234567890", app_secret="secret-123456"),
        current_user=test_user,
        db=db_session,
    )
    assert result["configured"] is True
    assert result["nickname"] == "测试公众号"
    row = db_session.query(UserWechatOfficialAccount).filter_by(user_id=test_user.id).one()
    assert row.app_secret_enc != "secret-123456"
    from backend.app.services import integration_secrets
    assert integration_secrets.decrypt(row.app_secret_enc) == "secret-123456"

    cfg = wechat_oa_publish.get_wechat_oa_config(current_user=test_user, db=db_session)
    assert cfg["configured"] is True
    assert cfg["secret_mask"]
    assert "secret-123456" not in str(cfg)


def test_send_draft_builds_and_posts(db_session, test_user, monkeypatch):
    from backend.app.api import wechat_oa_publish
    from backend.app.models import UserWechatOfficialAccount
    from backend.app.services import integration_secrets

    db_session.add(UserWechatOfficialAccount(
        user_id=test_user.id,
        app_id="wx1234567890",
        app_secret_enc=integration_secrets.encrypt("secret-123456"),
        nickname="测试公众号",
    ))
    db_session.commit()

    monkeypatch.setattr(wechat_oa_publish, "_get_access_token", lambda *args, **kwargs: "token")
    monkeypatch.setattr(wechat_oa_publish, "_download_image", lambda url: (b"img", "image/png", "a.png"))
    monkeypatch.setattr(wechat_oa_publish, "_upload_cover_material", lambda *args, **kwargs: "thumb-1")
    monkeypatch.setattr(wechat_oa_publish, "_upload_content_image", lambda *args, **kwargs: "https://wx.example.test/a.png")
    fake = _FakeClient(post_payload={"media_id": "draft-1"})
    monkeypatch.setattr(wechat_oa_publish.httpx, "Client", lambda **kwargs: fake)

    result = wechat_oa_publish.send_wechat_oa_draft(
        wechat_oa_publish.WechatOaDraftBody(
            title="文章标题",
            content="第一段\n第二段",
            digest="摘要",
            images=["https://img.example.test/a.png"],
        ),
        current_user=test_user,
        db=db_session,
    )
    assert result["ok"] is True
    assert result["media_id"] == "draft-1"
    assert result["image_count"] == 1
    posted = fake.calls[-1]["json"]
    assert posted["articles"][0]["thumb_media_id"] == "thumb-1"
    assert "https://wx.example.test/a.png" in posted["articles"][0]["content"]

def test_clean_content_text_removes_html_and_suggestions():
    from backend.app.api import wechat_oa_publish

    raw = '<div>&nbsp;</div><p>第一段正文</p><p><img src="https://img.example.test/a.jpg" /></p><p>配图建议：<br>1. 封面用人物图<br>2. 第二张用场景图</p><p>第二段正文</p>'
    cleaned = wechat_oa_publish._clean_content_text(raw)
    assert "<" not in cleaned
    assert "配图建议" not in cleaned
    assert "第一段正文" in cleaned
    assert "第二段正文" in cleaned
    assert cleaned.strip() == cleaned
    images = wechat_oa_publish._extract_content_images(raw)
    assert images == ["https://img.example.test/a.jpg"]

def test_default_server_ips_only_domestic(monkeypatch):
    from backend.app.api import wechat_oa_publish

    monkeypatch.delenv('WECHAT_OA_SERVER_IPS', raising=False)
    assert wechat_oa_publish._server_ips() == ['42.194.209.150']

