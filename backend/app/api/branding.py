from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.brand_context import public_brand_config, resolve_request_brand_mark


router = APIRouter()
_OEM_CODE_RE = re.compile(r"^[0-9]{4,12}$")
_OEM_ROOT = Path(__file__).resolve().parents[3] / "client_static" / "oem"
_OEM_MANIFEST_PATH = _OEM_ROOT / "manifest.json"
# Unified autostart shell. Every OEM folder keeps a renamed copy of its launcher
# EXE so the factory can wire boot autostart at one fixed file name, while the
# desktop shortcut keeps pointing at the branded EXE.
START_ENTRY_KEY = "start_entry"
START_ENTRY_FILENAME = "start.exe"


def _start_entry_asset(mark: str, assets: list) -> Optional[dict]:
    """Advertise ``<brand>/start.exe`` as long as it mirrors the launcher EXE.

    The copy is only delivered while it is byte-identical to this brand's launcher
    EXE, so the embedded brand icon (window, taskbar, file icon) can never drift
    away from the desktop EXE after a brand switch.
    """
    launcher = next(
        (
            item
            for item in assets
            if isinstance(item, dict) and str(item.get("key") or "").strip() == "launcher_exe"
        ),
        None,
    )
    if launcher is None:
        return None
    launcher_url = str(launcher.get("url") or "").strip()
    launcher_sha256 = str(launcher.get("sha256") or "").strip().lower()
    try:
        launcher_size = int(launcher.get("size"))
    except (TypeError, ValueError):
        return None
    if not launcher_url or "/" not in launcher_url or not re.fullmatch(r"[0-9a-f]{64}", launcher_sha256):
        return None
    try:
        data = (_OEM_ROOT / mark / START_ENTRY_FILENAME).read_bytes()
    except OSError:
        return None
    if len(data) != launcher_size or hashlib.sha256(data).hexdigest() != launcher_sha256:
        return None
    return {
        "key": START_ENTRY_KEY,
        "url": launcher_url.rsplit("/", 1)[0] + "/" + START_ENTRY_FILENAME,
        "size": launcher_size,
        "sha256": launcher_sha256,
    }


def _load_oem_manifest() -> dict:
    try:
        data = json.loads(_OEM_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="OEM 品牌清单不可用") from exc
    if not isinstance(data, dict) or not isinstance(data.get("codes"), dict) or not isinstance(data.get("brands"), dict):
        raise HTTPException(status_code=500, detail="OEM 品牌清单格式无效")
    return data


@router.get("/api/oem/bootstrap", summary="按 OEM 配置码获取 Online 品牌启动清单")
def get_oem_bootstrap(code: str = Query(...)):
    normalized_code = str(code or "").strip()
    if not _OEM_CODE_RE.fullmatch(normalized_code):
        raise HTTPException(status_code=400, detail="OEM 配置码格式无效")

    manifest = _load_oem_manifest()
    mark = str(manifest["codes"].get(normalized_code) or "").strip().lower()
    brand = manifest["brands"].get(mark)
    if not mark:
        raise HTTPException(status_code=404, detail="OEM 配置码未启用")
    if not isinstance(brand, dict):
        pending_brands = manifest.get("pending_brands")
        pending = pending_brands.get(mark) if isinstance(pending_brands, dict) else None
        display_name = str(pending.get("display_name") or "OEM 品牌") if isinstance(pending, dict) else "OEM 品牌"
        raise HTTPException(status_code=409, detail=f"{display_name}资源尚未配置，请提供 Logo 后再安装")

    profile = brand.get("profile")
    assets = brand.get("assets")
    if not isinstance(profile, dict) or not isinstance(assets, list):
        raise HTTPException(status_code=500, detail="OEM 品牌资源配置无效")
    normalized_assets = [item for item in assets if isinstance(item, dict)]
    start_entry = _start_entry_asset(mark, normalized_assets)
    if start_entry is not None:
        normalized_assets = [
            item
            for item in normalized_assets
            if str(item.get("key") or "").strip() != START_ENTRY_KEY
            and str(item.get("url") or "").rstrip("/").rsplit("/", 1)[-1].lower() != START_ENTRY_FILENAME
        ]
        normalized_assets.append(start_entry)
    return {
        "schema_version": int(manifest.get("schema_version") or 1),
        "oem_code": normalized_code,
        "brand_mark": mark,
        "version": str(brand.get("version") or "1"),
        "profile": profile,
        "assets": normalized_assets,
    }


@router.get("/api/branding", summary="获取当前 OEM 品牌配置")
def get_branding(
    request: Request,
    brand: Optional[str] = Query(None),
    brand_mark: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return public_brand_config(db, resolve_request_brand_mark(request, brand, brand_mark))
