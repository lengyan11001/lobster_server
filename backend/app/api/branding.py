from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.brand_context import public_brand_config, resolve_request_brand_mark


router = APIRouter()


@router.get("/api/branding", summary="获取当前 OEM 品牌配置")
def get_branding(
    request: Request,
    brand: Optional[str] = Query(None),
    brand_mark: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return public_brand_config(db, resolve_request_brand_mark(request, brand, brand_mark))
