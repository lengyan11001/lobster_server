#!/usr/bin/env python3
"""Verify the configured DATABASE_URL can boot ORM tables and answer basic counts."""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import settings  # noqa: E402
from backend.app.db import Base  # noqa: E402
from backend.app import models  # noqa: F401,E402


def main() -> int:
    url = (settings.database_url or "").strip()
    if not url:
        print("DATABASE_URL is empty", file=sys.stderr)
        return 2
    engine = create_engine(url, pool_pre_ping=True) if "sqlite" not in url.lower() else create_engine(url)
    insp = inspect(engine)
    missing = [t.name for t in Base.metadata.sorted_tables if not insp.has_table(t.name)]
    if missing:
        print("missing tables:", ", ".join(missing), file=sys.stderr)
        return 3
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            count = conn.execute(select(func.count()).select_from(table)).scalar()
            print(f"{table.name}: {count}")
    print("DATABASE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

