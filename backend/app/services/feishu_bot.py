"""飞书（Lark）推送底座：群机器人 webhook + 企业自建应用单聊。

两条通道，按需启用；不给凭证就自动跳过，绝不影响业务主流程：

1) 群机器人 webhook（最快落地）：往一个飞书群发消息卡片。
   配置：MANAGE_FEISHU_WEBHOOK（manage 项目/任务播报用）
   现有监控沿用 PROVIDER_BALANCE_FEISHU_WEBHOOK / RUNTIME_MONITOR_FEISHU_WEBHOOK。
2) 企业自建应用（单人私聊）：tenant_access_token → 按 open_id / email 给员工单独发。
   配置：FEISHU_APP_ID / FEISHU_APP_SECRET（放服务器 .env，不进 git）。

卡片格式与 provider_balance_monitor / runtime_monitor 完全一致
（msg_type=interactive + lark_md），所以同一个机器人能同时接监控和项目播报。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 10.0
_DEFAULT_API_BASE = "https://open.feishu.cn/open-apis"


def api_base() -> str:
    """飞书开放平台基址；私有化/自测可用 FEISHU_API_BASE 覆盖。"""
    return (_env("FEISHU_API_BASE") or _DEFAULT_API_BASE).rstrip("/")


ERROR_HINTS = {
    "10003": "app_id 不存在或不是这个企业的应用（确认用本企业账号建的应用）",
    "10014": "app_secret 不正确",
    "99991672": "权限未开通：开发者后台 → 权限管理 → 勾 im:message（+ contact:user.id:readonly）→ 创建版本并发布",
    "99991663": "应用未发布/未通过管理员审批，或可用范围不含该用户",
    "230002": "接收人不在应用可用范围内（开发者后台 → 版本与可用范围）",
    "230013": "接收人不在应用可用范围内",
}


def hint_for(err: object) -> str:
    """把飞书错误码翻译成一句可执行的处置建议（认不出来就返回空串）。"""
    text = str(err or "")
    for code, msg in ERROR_HINTS.items():
        if code in text:
            return msg
    return ""


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def env_bool(name: str, default: bool = True) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def webhook_url() -> str:
    return _env("MANAGE_FEISHU_WEBHOOK") or _env("LOBSTER_MANAGE_FEISHU_WEBHOOK")


def webhook_enabled() -> bool:
    return bool(webhook_url()) and env_bool("MANAGE_FEISHU_ENABLED", True)


def app_credentials() -> Optional[Tuple[str, str]]:
    app_id = _env("FEISHU_APP_ID")
    app_secret = _env("FEISHU_APP_SECRET")
    if app_id and app_secret:
        return app_id, app_secret
    return None


def app_enabled() -> bool:
    return app_credentials() is not None and env_bool("FEISHU_APP_ENABLED", True)


def build_card(title: str, lines: List[str], *, template: str = "blue", note: str = "",
               link: str = "", link_text: str = "打开管理台") -> Dict[str, Any]:
    """交互卡片：标题 + 多行 lark_md + 可选跳转按钮 + 备注。"""
    content = "\n".join([str(ln) for ln in lines if str(ln or "").strip()]) or "（暂无内容）"
    elements: List[Dict[str, Any]] = [{"tag": "div", "text": {"tag": "lark_md", "content": content}}]
    if link:
        elements.append({
            "tag": "action",
            "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": link_text},
                         "type": "primary", "url": link}],
        })
    if note:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        },
    }


def _check(body: Any, text: str, where: str) -> Dict[str, Any]:
    if isinstance(body, dict):
        code = body.get("code")
        if code not in (None, 0):
            raise RuntimeError("%s returned code=%s: %s" % (where, code, str(body)[:400]))
        return body
    return {"raw": str(text or body)[:400]}


async def _post(url: str, *, json_body: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
                client: Optional[httpx.AsyncClient] = None):
    if client is not None:
        return await client.post(url, json=json_body, headers=headers)
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SEC, trust_env=False) as c:
        return await c.post(url, json=json_body, headers=headers)


async def post_card(card: Dict[str, Any], *, webhook: str = "",
                    client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """把卡片发到群机器人 webhook。"""
    url = (webhook or webhook_url()).strip()
    if not url:
        raise RuntimeError("飞书群机器人 webhook 未配置（MANAGE_FEISHU_WEBHOOK）")
    resp = await _post(url, json_body=card, client=client)
    text = resp.text or ""
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {"raw": text[:400]}
    if resp.status_code >= 400:
        raise RuntimeError("Feishu webhook HTTP %s: %s" % (resp.status_code, text[:400]))
    return _check(body, text, "Feishu webhook")


async def send_card(title: str, lines: List[str], *, template: str = "blue", note: str = "",
                    link: str = "", link_text: str = "打开管理台", webhook: str = "",
                    client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """组装并发送；没配 webhook 或总开关关掉时返回 skipped（不抛错）。"""
    if not (webhook or webhook_url()) or not env_bool("MANAGE_FEISHU_ENABLED", True):
        return {"ok": False, "skipped": "feishu webhook not configured"}
    card = build_card(title, lines, template=template, note=note, link=link, link_text=link_text)
    body = await post_card(card, webhook=webhook, client=client)
    logger.info("[feishu] sent card: %s", title)
    return {"ok": True, "response": body}


async def fetch_tenant_access_token(*, app_id: str = "", app_secret: str = "",
                                    client: Optional[httpx.AsyncClient] = None) -> str:
    """换取 tenant_access_token。传公司自己的凭证就用公司的，否则回退到服务端 env。"""
    aid = str(app_id or "").strip()
    secret = str(app_secret or "").strip()
    if not (aid and secret):
        creds = app_credentials()
        if not creds:
            raise RuntimeError("飞书自建应用未配置（公司配置或 FEISHU_APP_ID / FEISHU_APP_SECRET）")
        aid, secret = creds
    url = api_base() + "/auth/v3/tenant_access_token/internal"
    resp = await _post(url, json_body={"app_id": aid, "app_secret": secret}, client=client)
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    if resp.status_code >= 400 or body.get("code") not in (0, None):
        raise RuntimeError("tenant_access_token failed: HTTP %s %s" % (resp.status_code, str(body)[:300]))
    token = str(body.get("tenant_access_token") or "")
    if not token:
        raise RuntimeError("tenant_access_token missing: %s" % str(body)[:300])
    return token


async def resolve_open_id(identifier: str = "", *, kind: str = "email", app_token: str = "",
                          app_id: str = "", app_secret: str = "",
                          client: Optional[httpx.AsyncClient] = None) -> str:
    """用手机号 / 企业邮箱换 open_id（员工身份用手机号登录，所以手机号是主路径）。"""
    text = str(identifier or "").strip()
    if not text:
        return ""
    token = app_token or await fetch_tenant_access_token(app_id=app_id, app_secret=app_secret, client=client)
    url = api_base() + "/contact/v3/users/batch_get_id?user_id_type=open_id"
    key = "mobiles" if str(kind or "").strip().lower() in ("mobile", "phone") else "emails"
    resp = await _post(url, json_body={key: [text]},
                       headers={"Authorization": "Bearer " + token}, client=client)
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    for row in ((body.get("data") or {}).get("user_list") or []):
        oid = str((row or {}).get("user_id") or "")
        if oid:
            return oid
    return ""


async def send_card_to_user(receive_id: str, card: Dict[str, Any], *,
                            receive_id_type: str = "open_id", app_token: str = "",
                            app_id: str = "", app_secret: str = "",
                            client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """给单个员工发私聊卡片。receive_id_type: open_id | user_id | email | union_id。"""
    rid = str(receive_id or "").strip()
    if not rid:
        raise RuntimeError("receive_id 为空")
    token = app_token or await fetch_tenant_access_token(app_id=app_id, app_secret=app_secret, client=client)
    url = api_base() + "/im/v1/messages?receive_id_type=" + receive_id_type
    payload = {"receive_id": rid, "msg_type": str(card.get("msg_type") or "interactive"),
               "content": json.dumps(card.get("card") or {}, ensure_ascii=False)}
    resp = await _post(url, json_body=payload,
                       headers={"Authorization": "Bearer " + token,
                                "Content-Type": "application/json"}, client=client)
    text = resp.text or ""
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {"raw": text[:400]}
    if resp.status_code >= 400:
        raise RuntimeError("Feishu send_to_user HTTP %s: %s" % (resp.status_code, text[:400]))
    return _check(body, text, "Feishu send_to_user")
