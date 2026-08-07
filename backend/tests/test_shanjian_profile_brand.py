from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api.shanjian_digital_human import ProfileTrainBody, _validated_profile_auth_text


def test_profile_auth_text_accepts_the_users_oem(monkeypatch):
    monkeypatch.setattr("backend.app.api.shanjian_digital_human.brand_short_name", lambda db, mark: "大咖")
    user = SimpleNamespace(brand_mark="daka")
    body = ProfileTrainBody(
        auth_text="我授权【大咖】使用视频中的肖像和声音。",
        brand_mark="daka",
    )

    auth_text, brand_mark = _validated_profile_auth_text(object(), user, body)

    assert auth_text == body.auth_text
    assert brand_mark == "daka"


@pytest.mark.parametrize(
    ("auth_text", "brand_mark"),
    [
        ("我授权本平台使用视频中的肖像和声音。", "daka"),
        ("我授权【必火】使用视频中的肖像和声音。", "daka"),
        ("我授权【大咖】使用视频中的肖像和声音。", "bihuo"),
    ],
)
def test_profile_auth_text_rejects_generic_or_cross_oem_copy(monkeypatch, auth_text, brand_mark):
    monkeypatch.setattr("backend.app.api.shanjian_digital_human.brand_short_name", lambda db, mark: "大咖")
    user = SimpleNamespace(brand_mark="daka")
    body = ProfileTrainBody(auth_text=auth_text, brand_mark=brand_mark)

    with pytest.raises(HTTPException) as exc_info:
        _validated_profile_auth_text(object(), user, body)

    assert exc_info.value.status_code == 400
