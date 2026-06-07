"""User settings: model selection, preferences."""
import json
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from .auth import get_current_user
from ..models import User

router = APIRouter()

_CUSTOM_CONFIGS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "custom_configs.json"


def _read_server_tos_config_dict() -> Optional[Dict[str, Any]]:
    """Read server-side TOS_CONFIG for status checks; never return AK/SK to clients."""
    if not _CUSTOM_CONFIGS_FILE.exists():
        return None
    try:
        data = json.loads(_CUSTOM_CONFIGS_FILE.read_text(encoding="utf-8"))
        cfg = (data.get("configs") or {}).get("TOS_CONFIG")
        if not isinstance(cfg, dict):
            return None
        ak = str(cfg.get("access_key", "")).strip()
        sk = str(cfg.get("secret_key", "")).strip()
        if not ak or not sk:
            return None
        return cfg
    except Exception:
        return None


def _use_own_wechat_login() -> bool:
    return bool((getattr(settings, "wechat_app_id", None) or "").strip() and (getattr(settings, "wechat_app_secret", None) or "").strip())


def _use_fuiou_pay() -> bool:
    from ..services.fuiou_pay import fuiou_configured
    return fuiou_configured()


@router.get("/api/edition", summary="当前版本（本构建仅在线版）")
def get_edition():
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition != "online":
        edition = "online"
    out = {"edition": edition}
    use_independent = getattr(settings, "lobster_independent_auth", True)
    out["use_independent_auth"] = bool(use_independent)
    out["use_own_wechat_login"] = _use_own_wechat_login()
    out["use_fuiou_pay"] = _use_fuiou_pay()
    if edition == "online":
        out["allow_self_config_model"] = getattr(settings, "sutui_online_model_self_config", True)
        if not use_independent:
            out["recharge_url"] = (getattr(settings, "sutui_recharge_url", None) or "").strip() or None
    return out


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class UpdateSettingsRequest(BaseModel):
    preferred_model: Optional[str] = None


@router.get("/api/settings", summary="获取用户设置")
def get_settings(current_user: User = Depends(get_current_user)):
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition == "online":
        preferred = "sutui"
    else:
        preferred = getattr(current_user, "preferred_model", "openclaw") or "openclaw"
    return {"preferred_model": preferred}


@router.post("/api/settings", summary="更新用户设置")
def update_settings(
    body: UpdateSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.preferred_model is not None:
        current_user.preferred_model = body.preferred_model.strip() or "openclaw"
    db.commit()
    return {"preferred_model": current_user.preferred_model}


@router.get("/api/settings/models", summary="可选模型列表（需登录）")
def list_models(current_user: User = Depends(get_current_user)):
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition == "online":
        return {
            "models": [
                {
                    "id": "sutui_aggregate",
                    "name": "速推聚合",
                    "description": "速推多模型；进入智能会话后在子下拉选择具体模型",
                }
            ]
        }

    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    models = []

    config_path = base_dir / "models_config.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            models = data.get("models", [])
        except Exception:
            pass
    if not models:
        models = [
            {"id": "openclaw", "name": "默认 (OpenClaw)", "description": "OpenClaw 默认路由"},
            {"id": "anthropic/claude-sonnet-4-5", "name": "Claude Sonnet 4.5", "description": "Anthropic 快速模型"},
            {"id": "openai/gpt-4o", "name": "GPT-4o", "description": "OpenAI 多模态模型"},
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "description": "DeepSeek 对话模型"},
        ]

    existing_ids = {m.get("id") for m in models}

    custom_path = base_dir / "custom_configs.json"
    if custom_path.exists():
        try:
            custom_data = json.loads(custom_path.read_text(encoding="utf-8"))
            for cm in custom_data.get("custom_models", []):
                mid = cm.get("model_id", "")
                if mid and mid not in existing_ids:
                    models.append({
                        "id": mid,
                        "name": cm.get("display_name") or mid,
                        "description": cm.get("provider", "自定义模型"),
                        "custom": True,
                    })
                    existing_ids.add(mid)
        except Exception:
            pass

    return {"models": models}


@router.get(
    "/api/settings/tos-config",
    summary="TOS 上传模式状态（需登录）",
)
def get_tos_config_for_online_client(current_user: User = Depends(get_current_user)):
    """Return server-side upload status and keep old online clients compatible."""
    cfg = _read_server_tos_config_dict()
    if not cfg:
        raise HTTPException(
            status_code=404,
            detail="服务器未在 custom_configs.json 中配置有效 TOS_CONFIG（需 access_key/secret_key 等）",
        )
    return {
        "ok": True,
        "mode": "server-side-upload",
        "tos_configured": True,
        "bucket_name": str(cfg.get("bucket_name") or ""),
        "public_domain": str(cfg.get("public_domain") or ""),
        "TOS_CONFIG": cfg,
    }


@router.get("/api/settings/lan-info", summary="获取局域网访问信息（需登录）")
def get_lan_info(current_user: User = Depends(get_current_user)):
    ip = _get_lan_ip()
    port = getattr(settings, "port", 8000)
    return {
        "lan_ip": ip,
        "port": port,
        "url": f"http://{ip}:{port}",
    }
