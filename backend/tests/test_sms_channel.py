import json
from types import SimpleNamespace

from backend.app.services.sms_channel import resolve_aliyun_sms_channel


def _settings(**overrides):
    values = {
        "aliyun_sms_access_key_id": "global-ak",
        "aliyun_sms_access_key_secret": "global-sk",
        "aliyun_sms_sign_name": "global-sign",
        "aliyun_sms_template_code": "SMS_GLOBAL",
        "aliyun_sms_brand_channels_json": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_brand_channel_overrides_global_settings():
    settings = _settings(
        aliyun_sms_brand_channels_json=json.dumps(
            {"hikong": {
                "access_key_id": "hikong-ak",
                "access_key_secret": "hikong-sk",
                "sign_name": "海康智慧科技深圳",
                "template_code": "SMS_511855264",
            }}
        )
    )

    channel = resolve_aliyun_sms_channel("HiKong", settings)

    assert channel.access_key_id == "hikong-ak"
    assert channel.access_key_secret == "hikong-sk"
    assert channel.sign_name == "海康智慧科技深圳"
    assert channel.template_code == "SMS_511855264"


def test_unknown_brand_falls_back_to_global_settings():
    channel = resolve_aliyun_sms_channel("daka", _settings())

    assert channel.access_key_id == "global-ak"
    assert channel.sign_name == "global-sign"
    assert channel.template_code == "SMS_GLOBAL"


def test_brand_environment_variables_override_json(monkeypatch):
    settings = _settings(
        aliyun_sms_brand_channels_json=json.dumps(
            {"hikong": {"sign_name": "json-sign", "template_code": "JSON_TEMPLATE"}}
        )
    )
    monkeypatch.setenv("ALIYUN_SMS_HIKONG_SIGN_NAME", "env-sign")

    channel = resolve_aliyun_sms_channel("hikong", settings)

    assert channel.sign_name == "env-sign"
    assert channel.template_code == "JSON_TEMPLATE"


def test_partial_brand_configuration_does_not_fall_back_to_global():
    settings = _settings(
        aliyun_sms_brand_channels_json=json.dumps({"hikong": {"sign_name": "海康智慧科技深圳"}})
    )

    channel = resolve_aliyun_sms_channel("hikong", settings)

    assert channel.brand_specific is True
    assert channel.access_key_id == ""
    assert channel.access_key_secret == ""
    assert channel.template_code == ""
    assert channel.ready is False
