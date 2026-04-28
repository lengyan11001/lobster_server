from __future__ import annotations

import socket
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MCP/脚本会单独读 os.environ（如 SUTUI_SERVER_TOKENS_*）；此处 extra=ignore 避免 .env 多出的键导致启动失败。"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    app_name: str = "龙虾 (Lobster)"
    debug: bool = True
    secret_key: str = "lobster-secret-change-me"
    cors_origins: str = "*"
    database_url: str = "sqlite:///./lobster.db"
    # MySQL/PostgreSQL 连接池（SQLite 忽略）
    db_pool_size: int = 15
    db_max_overflow: int = 25
    db_pool_timeout: int = 60
    db_pool_recycle: int = 280
    host: str = "0.0.0.0"
    port: int = 8000
    """微信/支付回调根地址。不填时自动用本机 LAN IP:PORT；服务器仅公网 IP 无域名时填 http://公网IP:8000。勿填 127.0.0.1 作多设备素材预览根。"""
    public_base_url: Optional[str] = None
    """素材签名 URL：局域网/公网可访问根地址。PUBLIC_BASE_URL 为回环或未设时用于 /api/assets/file 预览链。"""
    lan_public_base_url: Optional[str] = None
    mcp_port: int = 8001
    """本构建统一为在线版：online（独立登录/注册或速推扫码，速推 Token 来自登录）。"""
    lobster_edition: str = "online"
    """在线版为 True 时：登录注册与充值全部自维护，不走速推；速推算力由服务器配置的 SUTUI_SERVER_TOKEN(S) 负载均衡，扣用户积分。"""
    lobster_independent_auth: bool = True
    """逗号分隔登录账号（与 User.email 存的一致），额外视为技能商店管理员（可见 store_visibility=debug 的包、调试用能力）。与代码内建白名单合并。"""
    lobster_skill_store_admin_accounts: Optional[str] = None
    """完成充值订单时需在请求头 X-Admin-Secret 携带此值（仅服务端/管理员使用）。"""
    lobster_recharge_admin_secret: Optional[str] = None
    """充值创建订单后展示给用户的付款说明。"""
    lobster_recharge_payment_hint: Optional[str] = None
    """仅用于测试或脚本；在线版不创建默认用户。"""
    default_user_email: str = "user@lobster.local"
    default_user_password: str = "lobster123"
    """在线版：速推 OAuth 登录页 URL，登录成功后跳转到 /auth/sutui-callback?token=xxx"""
    sutui_oauth_login_url: Optional[str] = None
    """速推 API 根地址，用于 apikeys/list、balance 等（仅 online 使用）"""
    sutui_api_base: str = "https://api.xskill.ai"
    # sutui-chat：docs 无定价时按上游 usage 每千 token 事后扣费（ceil）；0=禁用兜底
    sutui_chat_fallback_credits_per_1k: float = 1.0
    """可选，JSON 对象：按 model id 覆盖「无 docs、无 x_billing」时的每千 token 积分单价，与内置表合并（同名键以此为准）。例：{"deepseek-chat":0.055}。速推 /v3/models/{id}/docs 公开列表常不含 LLM，流式又无 x_billing 时依赖此项或内置。"""
    sutui_chat_usage_credits_per_1k_by_model_json: Optional[str] = None
    """服务器侧速推 Token：能力由服务器转发时使用，用户不直接走速推。MCP 从环境变量 SUTUI_SERVER_TOKEN 读取。"""
    sutui_server_token: Optional[str] = None
    """我方标识，登录时带在 URL 上供速推统计（仅 online 使用）"""
    sutui_source_id: Optional[str] = None
    """充值页链接，前端「充值」按钮跳转（仅 online 使用）"""
    sutui_recharge_url: Optional[str] = None
    """是否允许 online 用户自配模型 Key；False 时统一走速推服务端模型（仅 online 使用）"""
    sutui_online_model_self_config: bool = True
    """下发给 lobster_online 的默认图片生成模型；客户端拉取失败时才使用本地兜底。"""
    lobster_default_image_generate_model: str = "gpt-image2"
    """下发给 lobster_online 的默认视频生成模型；客户端拉取失败时才使用本地兜底。"""
    lobster_default_video_generate_model: str = "veo3.1-fast"
    """已废弃：前端由 lobster_online 提供，本服务仅 API，不再挂载 /static 与前端页。保留项以免旧 .env 报错。"""
    serve_frontend: bool = False
    # 自建微信登录（不用速推）：小程序 appid/secret，配置后登录页展示小程序码扫码
    wechat_app_id: Optional[str] = None
    wechat_app_secret: Optional[str] = None
    """小程序码跳转的页面路径，如 pages/index/index，扫码后打开该页并带 scene"""
    wechat_miniprogram_page: Optional[str] = None
    """服务号网页授权（与小程序二选一或并存）：AppID/AppSecret，配置后登录页返回 login_url 供扫码"""
    wechat_oa_app_id: Optional[str] = None
    wechat_oa_secret: Optional[str] = None
    """服务号回调根地址，不填则用 public_base_url 或 request.base_url"""
    wechat_oa_base_url: Optional[str] = None
    """扫码成功后跳转的前端地址（带 ?token= 自动登录）。不填则用 wechat_oa_base_url，需该地址提供带 ?token= 逻辑的前端"""
    wechat_oa_frontend_url: Optional[str] = None
    """服务号消息推送：服务器配置里的 Token（GET 验证用）"""
    wechat_oa_token: Optional[str] = None
    """服务号消息推送：EncodingAESKey（明文模式可不参与解密）"""
    wechat_oa_encoding_aes_key: Optional[str] = None
    # ── 富友聚合支付（MD5 签名；主扫 preCreate / 查询 commonQuery）──
    # 文档: https://fundwx.fuiou.com/doc/#/aggregatePay/api
    """富友商户号 mchnt_cd（富友入网得到，长度 ≤15）"""
    fuiou_mchnt_cd: Optional[str] = None
    """富友商户密钥 mchnt_key（MD5 签名用，系统分配）"""
    fuiou_mchnt_key: Optional[str] = None
    """统一下单（主扫）接口 URL"""
    fuiou_precreate_url: Optional[str] = None
    """订单查询接口 URL"""
    fuiou_query_url: Optional[str] = None
    """退款接口 URL（可选）"""
    fuiou_refund_url: Optional[str] = None
    """终端号 term_id（可选，默认 88888888）"""
    fuiou_term_id: Optional[str] = None
    """默认订单类型：WECHAT / ALIPAY / UNIONPAY"""
    fuiou_default_order_type: str = "WECHAT"
    """订单前缀（5位，富友分配）"""
    fuiou_order_prefix: Optional[str] = None
    """机构号 ins_cd（可选，富友分配）"""
    fuiou_ins_cd: Optional[str] = None
    """是否启用幂等重复下单（Reserved_repeat_order=1）"""
    fuiou_repeat_order: Optional[str] = None
    openclaw_gateway_url: Optional[str] = None
    openclaw_gateway_token: Optional[str] = None
    openclaw_agent_id: str = "main"
    """启动时是否尝试在本机拉起 OpenClaw Gateway（需 node + openclaw.mjs）。纯 API 的 Linux 服务器无此文件时可设 false，避免无意义日志。"""
    openclaw_autostart: bool = True
    """true=对话默认先走 OpenClaw Gateway，直连 LLM+MCP 作为兜底。"""
    lobster_openclaw_primary_chat: bool = False
    """true=对话只走 OpenClaw Gateway，不 fallback 到直连 LLM。"""
    lobster_openclaw_only_chat: bool = False
    """true=用户消息以 /openclaw 开头时该轮强制走 OpenClaw Gateway。"""
    lobster_openclaw_chat_prefix_gate: bool = True
    """本地轮询拉取/提交回复时的鉴权：请求头 X-Forward-Secret 需与此一致。不设则不做校验（仅内网或隧道时建议设置）。"""
    wecom_forward_secret: Optional[str] = None
    # ── Comfly 中转平台（与速推并行的生成能力上游）──
    comfly_api_base: Optional[str] = None
    comfly_api_key: Optional[str] = None
    capability_sutui_mcp_url: Optional[str] = None
    capability_upstream_urls_json: Optional[str] = None
    reddit_comment2video_backend_url: Optional[str] = None
    # 预留：大陆 API 转发 Messenger CRUD 至海外（未实现 HTTP 转发时勿依赖）
    messenger_upstream_url: Optional[str] = None
    # 海外实例：与大陆共用 SECRET_KEY 时，库中无 users 行仍信任 JWT sub 作为 messenger_configs.user_id
    messenger_trust_jwt_without_user: bool = False
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_whatsapp_webhook_full_url: Optional[str] = None
    """MCP 调用 /capabilities/* 计费接口时携带请求头 X-Lobster-Mcp-Billing 与此值一致；与来源为 127.0.0.1/::1 二选一即可改余额。未设时仅允许本机回环，避免公网与本机 MCP 重复扣费。"""
    lobster_mcp_billing_internal_key: Optional[str] = None
    """管理后台登录账号（不配置则管理后台不可用）。"""
    lobster_admin_username: Optional[str] = None
    """管理后台登录密码。"""
    lobster_admin_password: Optional[str] = None
    """互亿无线短信 APIID（用户中心-文本短信-产品总览）"""
    ihuyi_sms_account: Optional[str] = None
    """互亿无线 APIKEY，对应 Submit.json 请求参数 password"""
    ihuyi_sms_password: Optional[str] = None
    """阿里云短信 AccessKey ID"""
    aliyun_sms_access_key_id: Optional[str] = None
    """阿里云短信 AccessKey Secret"""
    aliyun_sms_access_key_secret: Optional[str] = None
    """阿里云短信签名名称"""
    aliyun_sms_sign_name: str = "深圳市必火智能信息技术"
    """阿里云短信模板Code"""
    aliyun_sms_template_code: str = "SMS_333406023"

    # ── 直连 LLM API（优先于 xskill.ai 中转）──
    deepseek_api_key: Optional[str] = None
    deepseek_api_base: str = "https://api.deepseek.com"

    # ── Meta Social（Instagram / Facebook 发布）──
    """Facebook App ID（在 developers.facebook.com 创建 App 后获取）"""
    meta_app_id: Optional[str] = None
    """Facebook App Secret"""
    meta_app_secret: Optional[str] = None

    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def get_effective_public_base_url() -> str:
    """微信/支付回调等用的根地址。未配置 PUBLIC_BASE_URL 时用本机 LAN IP + PORT（本地或服务器仅 IP 时可直接用）。"""
    base = (getattr(settings, "public_base_url", None) or "").strip().rstrip("/")
    if base:
        return base
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    port = getattr(settings, "port", 8000)
    return f"http://{ip}:{port}"
