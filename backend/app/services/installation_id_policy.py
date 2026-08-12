from __future__ import annotations

from typing import Optional


# These IDs were generated/persisted by the H5 slot auto-claim bug and then
# reused across many unrelated accounts. Never trust them as device identity.
DEPRECATED_INSTALLATION_IDS = frozenset(
    {
        "2fc3f43f7a684411a442cb661898aa74",
        "fa2d09cfbd9c4b2380352906225f2817",
    }
)


def raw_installation_id(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if "--" in text:
        return text.split("--", 1)[1]
    return text


def is_deprecated_installation_id(value: Optional[str]) -> bool:
    return raw_installation_id(value) in DEPRECATED_INSTALLATION_IDS
