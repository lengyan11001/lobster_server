"""微信服务号：服务器配置（GET 验证 + POST 接收消息）。消息推送到此地址。"""
import hashlib
import logging
import time
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from ..core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _build_text_reply_xml(to_user: str, from_user: str, content: str) -> str:
    t = str(int(time.time()))
    return (
        "<xml>"
        "<ToUserName><![CDATA[{}]]></ToUserName>"
        "<FromUserName><![CDATA[{}]]></FromUserName>"
        "<CreateTime>{}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[{}]]></Content>"
        "</xml>"
    ).format(to_user, from_user, t, content)


def _get_token() -> str:
    return (getattr(settings, "wechat_oa_token", None) or "").strip()


@router.get("/api/wechat", summary="服务号服务器配置：微信 GET 验证")
def wechat_verify(
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
):
    """微信服务器验证：token+timestamp+nonce 字典序排序后拼接做 SHA1，与 signature 一致则原样返回 echostr。"""
    token = _get_token()
    if not token:
        logger.warning("[api/wechat] GET 验证失败: 未配置 WECHAT_OA_TOKEN")
        return PlainTextResponse("", status_code=403)
    lst = sorted([token, timestamp, nonce])
    s = "".join(lst)
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    if h != signature:
        logger.warning("[api/wechat] GET 验证失败: signature 不匹配 (token 与公众平台填写是否一致?)")
        return PlainTextResponse("", status_code=403)
    logger.info("[api/wechat] GET 验证成功, 返回 echostr")
    return PlainTextResponse(echostr or "")


@router.post("/api/wechat", summary="服务号服务器配置：接收消息（明文模式）")
async def wechat_message(request: Request):
    """明文模式：body 为 XML；文本消息走与企微一致的渠道回复（须用户已微信登录绑定 wechat_openid，按该用户扣费）。"""
    token = _get_token()
    if not token:
        return Response(status_code=403)
    try:
        body = await request.body()
        if not body:
            return Response(status_code=200, content="", media_type="text/plain")
        root = ET.fromstring(body.decode("utf-8"))
        msg_type = root.find("MsgType")
        from_user = root.find("FromUserName")
        to_user = root.find("ToUserName")
        content = root.find("Content")
        msg_type_text = (msg_type.text if msg_type is not None else "") or ""
        from_text = (from_user.text if from_user is not None else "") or ""
        to_text = (to_user.text if to_user is not None else "") or ""
        content_text = (content.text if content is not None else "") or ""
        logger.info("[微信服务号] 收到消息 type=%s From=%s To=%s Content=%s", msg_type_text, from_text, to_text, content_text[:80] if content_text else "")
        if msg_type_text.lower() == "text" and from_text and to_text and (content_text.strip()):
            from .chat import get_reply_for_channel

            reply_text = await get_reply_for_channel(
                content_text.strip(),
                session_id=f"wechat_oa_{from_text}",
                channel_system="你是微信服务号助手。根据用户消息简短、友好地回复。使用中文。",
                channel="wechat_oa",
                from_user=from_text,
            )
            xml = _build_text_reply_xml(from_text, to_text, reply_text or "收到。")
            return Response(content=xml.encode("utf-8"), media_type="application/xml; charset=utf-8")
    except Exception as e:
        logger.warning("[微信服务号] 解析 POST body 失败: %s", e)
    return Response(status_code=200, content="", media_type="text/plain")
