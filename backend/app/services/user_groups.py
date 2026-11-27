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
        materialize_group_permissions(db)
    except Exception:  # noqa: BLE001
        db.rollback()


def _server_is_overseas() -> bool:
    try:
        from .tos_storage import is_overseas_server

        return bool(is_overseas_server())
    except Exception:
        return False


def materialize_group_permissions(db: Session) -> int:
    """把「未配置」的分组落成显式清单。

    后台展示的勾选必须就是真正生效的分组权限：permissions 为 NULL 时后台
    只能展示默认基线，用户会误以为已经保存。这里在分组创建/启动时把它写成
    显式列表（基础/中级=本环境默认基线，高级=全部），之后看到的勾选即生效。
    """
    changed = 0
    overseas = _server_is_overseas()
    for row in db.query(UserGroup).all():
        if row.permissions is not None:
            continue
        code = normalize_group_code(row.code)
        if not code:
            continue
        row.permissions = (
            all_package_permissions() if code == "advanced" else default_permissions(is_overseas=overseas)
        )
        changed += 1
    if changed:
        db.commit()
    return changed


def all_package_permissions() -> list[str]:
    """高级分组未显式配置时，按「全功能」返回技能商店全部包。"""
    try:
        from ..api.skills import _expand_group_visibility, _load_registry

        ids: set[str] = set()
        for pkg in (_load_registry().get("packages") or []):
            if isinstance(pkg, dict):
                pid = str(pkg.get("id") or pkg.get("package_id") or "").strip()
            else:
                pid = str(pkg).strip()
            if pid:
                ids.add(pid)
        return sorted(_expand_group_visibility(ids))
    except Exception:
        return []


def permissions_for_group_code(
    db: Session, code: str, *, is_overseas: bool = False
) -> Optional[list[str]]:
    """分组权限（唯一口径，2026-11-27 用户再次确认）：

    - 没有任何分组的用户：返回 None，由调用方使用「该用户自己的个人权限」；
    - 有分组的用户：一律以分组为准，不再看个人权限；
      分组没单独配权限时，基础/中级用该环境默认基线，高级=全功能。
    """
    clean = normalize_group_code(code)
    if not clean:
        return None
    row = db.query(UserGroup).filter(UserGroup.code == clean).first()
    if row is None or row.permissions is None:
        if clean == "advanced":
            return all_package_permissions()
        return default_permissions(is_overseas=is_overseas)
    return [str(item).strip() for item in (row.permissions or []) if str(item).strip()]


def group_permissions(db: Session, user: User) -> Optional[list[str]]:
    """返回该用户分组的权限列表；未分组才返回 None（回落到个人权限）。"""
    code = normalize_group_code(getattr(user, "group_code", ""))
    if not code:
        return None
    return permissions_for_group_code(
        db, code, is_overseas=bool(getattr(user, "is_overseas_user", False))
    )


def default_permissions(is_overseas: bool = False) -> list[str]:
    """分组还没配置时，后台展示/回落用的默认基线。"""
    from ..api.skills import _default_visible_packages_for_request, _expand_group_visibility

    return sorted(_expand_group_visibility(set(_default_visible_packages_for_request(bool(is_overseas)))))
