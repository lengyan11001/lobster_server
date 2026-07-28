from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.brand_context import DEFAULT_BRAND_MARK, public_brand_config


router = APIRouter()


@router.get("/api/branding", summary="获取当前 OEM 品牌配置")
def get_branding(
    brand: Optional[str] = Query(None),
    brand_mark: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return public_brand_config(db, brand or brand_mark or DEFAULT_BRAND_MARK)
