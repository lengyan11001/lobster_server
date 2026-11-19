"""用户分组：基础 / 中级 / 高级。

口径（2026-11-19 用户明确）：
- 新用户注册默认进入「基础」分组；
- 设置了分组的用户，技能权限完全按分组权限走；
- 老用户分组为空时，继续沿用自己身上的 UserSkillVisibility 权限。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import User, UserGroup

DEFAULT_GROUP_CODE = "basic"

GROUP_DEFS: tuple[tuple[str, str, str], ...] = (
    ("basic", "基础", "新注册用户默认进入；只开放基础能力"),
    ("intermediate", "中级", "进阶用户；开放更多营销与获客能力"),
    ("advanced", "高级", "全功能用户；开放全部上架能力"),
)

GROUP_CODES = frozenset(code for code, _, _ in GROUP_DEFS)
GROUP_LABELS = {code: name for code, name, _ in GROUP_DEFS}


def normalize_group_code(value: Any) -> str:
    """只接受三个固定分组；空/未知一律视为未分组。"""
    code = str(value or "").strip().lower()
    return code if code in GROUP_CODES else ""


def ensure_user_groups(db: Session) -> None:
    """保证三条分组记录存在；已存在的不覆盖后台配置。"""
    try:
        existing = {str(row[0]).strip().lower() for row in db.query(UserGroup.code).all()}
        added = False
        for code, name, description in GROUP_DEFS:
            if code in existing:
                continue
            db.add(UserGroup(code=code, name=name, description=description, permissions=None))
            added = True
        if added:
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def group_permissions(db: Session, user: User) -> Optional[list[str]]:
    """返回该用户分组的权限列表；None 表示「未分组/未配置」，调用方应回落到个人权限。"""
    code = normalize_group_code(getattr(user, "group_code", ""))
    if not code:
        return None
    row = db.query(UserGroup).filter(UserGroup.code == code).first()
    if row is None or row.permissions is None:
        return None
    return [str(item).strip() for item in (row.permissions or []) if str(item).strip()]


def default_permissions(is_overseas: bool = False) -> list[str]:
    """分组还没配置时，后台展示/回落用的默认基线。"""
    from ..api.skills import _default_visible_packages_for_request, _expand_group_visibility

    return sorted(_expand_group_visibility(set(_default_visible_packages_for_request(bool(is_overseas)))))
