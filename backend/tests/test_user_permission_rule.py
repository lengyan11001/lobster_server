"""权限口径回归测试（2026-11-27 用户再次确认的规则）。

唯一口径：
- 没有分组的用户（老用户）       -> 用自己的个人权限（UserSkillVisibility）；
- 有分组的用户（新用户/已分组）  -> 只看分组权限，完全忽略个人权限；
  - 分组没单独配权限：基础/中级 = 该环境默认基线，高级 = 全部技能包。

这个分支历史上被改错过两次（未配置分组时错误地回落到个人权限 / 反过来
把个人权限整个忽略），所以用测试锁死。
"""
from __future__ import annotations

import pytest as _pytest  # noqa: F401  (保持与其他测试一致的依赖)

from backend.app.services import user_groups as ug


class _Row:
    def __init__(self, permissions):
        self.permissions = permissions


class _Query:
    def __init__(self, row):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _DB:
    def __init__(self, row):
        self._row = row

    def query(self, *args, **kwargs):
        return _Query(self._row)


def _db_with(permissions):
    return _DB(_Row(permissions))


def test_unconfigured_basic_uses_baseline_not_personal(monkeypatch):
    monkeypatch.setattr(ug, "default_permissions", lambda is_overseas=False: ["baseline"])
    assert ug.permissions_for_group_code(_db_with(None), "basic") == ["baseline"]


def test_unconfigured_intermediate_uses_baseline(monkeypatch):
    monkeypatch.setattr(ug, "default_permissions", lambda is_overseas=False: ["baseline"])
    assert ug.permissions_for_group_code(_db_with(None), "intermediate") == ["baseline"]


def test_unconfigured_advanced_is_full(monkeypatch):
    monkeypatch.setattr(ug, "all_package_permissions", lambda: ["everything"])
    assert ug.permissions_for_group_code(_db_with(None), "advanced") == ["everything"]


def test_configured_group_wins_over_baseline(monkeypatch):
    monkeypatch.setattr(ug, "default_permissions", lambda is_overseas=False: ["baseline"])
    assert ug.permissions_for_group_code(_db_with(["a", "b"]), "basic") == ["a", "b"]


def test_ungrouped_returns_none_so_caller_uses_personal_permissions():
    assert ug.permissions_for_group_code(_db_with(["ignored"]), "") is None



def test_new_user_defaults_to_basic_group():
    """新注册用户默认进基础分组（ORM 插入默认值）；历史行不会被改写。"""
    from backend.app.models import User

    col = User.__table__.c.group_code
    assert col.default is not None and col.default.arg == "basic"
    assert col.server_default is None or str(col.server_default.arg or "") == ""
