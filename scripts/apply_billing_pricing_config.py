#!/usr/bin/env python3
"""Safely update billing credit packages in custom_configs.json.

This script is intended to run on the server. It preserves all existing
custom config keys and only replaces configs.BILLING_PRICING.credit_packages.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


CREDIT_PACKAGES = [
    {"price_yuan": 100, "credits": 10000, "label": "100元 - 10000算力"},
    {"price_yuan": 200, "credits": 20000, "label": "200元 - 20000算力"},
    {"price_yuan": 500, "credits": 50000, "label": "500元 - 50000算力"},
    {"price_yuan": 1000, "credits": 100000, "label": "1000元 - 100000算力"},
]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    path = root / "custom_configs.json"
    data = _load_json(path)

    configs = data.get("configs")
    if not isinstance(configs, dict):
        configs = {}
        data["configs"] = configs

    billing = configs.get("BILLING_PRICING")
    if not isinstance(billing, dict):
        billing = {}
        configs["BILLING_PRICING"] = billing

    before = billing.get("credit_packages")
    billing["credit_packages"] = CREDIT_PACKAGES

    if before == CREDIT_PACKAGES and path.exists():
        print("custom_configs.json already has expected credit packages")
        return 0

    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = path.with_name(f"{path.name}.bak.{stamp}")
        shutil.copy2(path, backup)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("updated custom_configs.json billing credit_packages")
    if backup is not None:
        print(f"backup={backup.name}")
    print(json.dumps(CREDIT_PACKAGES, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
