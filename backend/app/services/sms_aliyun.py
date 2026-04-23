"""阿里云短信（验证码），配置见 ALIYUN_SMS_ACCESS_KEY_ID / ALIYUN_SMS_ACCESS_KEY_SECRET。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def send_verify_code_sms(
    *,
    access_key_id: str,
    access_key_secret: str,
    sign_name: str,
    template_code: str,
    mobile: str,
    code: str,
) -> Dict[str, Any]:
    from alibabacloud_dysmsapi20170525.client import Client as Dysmsapi20170525Client
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_dysmsapi20170525 import models as dysmsapi_20170525_models
    from alibabacloud_tea_util import models as util_models

    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
    )
    config.endpoint = "dysmsapi.aliyuncs.com"
    client = Dysmsapi20170525Client(config)

    req = dysmsapi_20170525_models.SendSmsRequest(
        sign_name=sign_name,
        template_code=template_code,
        phone_numbers=mobile,
        template_param=json.dumps({"code": code}),
    )
    runtime = util_models.RuntimeOptions()
    try:
        resp = client.send_sms_with_options(req, runtime)
        body = resp.body
        if body and body.code == "OK":
            logger.info("aliyun sms sent ok: mobile=%s biz_id=%s", mobile, body.biz_id)
            return {"code": "OK", "biz_id": body.biz_id, "message": body.message}
        msg = body.message if body else "未知错误"
        logger.warning("aliyun sms business error: code=%s msg=%s", body.code if body else "?", msg)
        raise RuntimeError(f"短信发送失败: {msg}")
    except RuntimeError:
        raise
    except Exception as e:
        logger.exception("aliyun sms request failed: %s", e)
        raise RuntimeError("短信通道请求失败，请稍后重试") from e
