from __future__ import annotations

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
_OEM_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "client_static" / "oem" / "manifest.json"


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
    return {
        "schema_version": int(manifest.get("schema_version") or 1),
        "oem_code": normalized_code,
        "brand_mark": mark,
        "version": str(brand.get("version") or "1"),
        "profile": profile,
        "assets": assets,
    }


@router.get("/api/branding", summary="获取当前 OEM 品牌配置")
def get_branding(
    request: Request,
    brand: Optional[str] = Query(None),
    brand_mark: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return public_brand_config(db, resolve_request_brand_mark(request, brand, brand_mark))
