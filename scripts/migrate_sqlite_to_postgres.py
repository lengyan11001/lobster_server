#!/usr/bin/env python3
"""Migrate Lobster server data from SQLite to PostgreSQL.

This script is intentionally conservative:
- it never modifies the SQLite source database;
- it can create ORM tables on PostgreSQL;
- it can drop and recreate PostgreSQL ORM tables only when --drop-target is explicitly set;
- it truncates PostgreSQL tables only when --truncate-target is explicitly set;
- it copies rows table-by-table in dependency order and resets PostgreSQL sequences;
- it prints source/target row counts for verification.

Example:
  python scripts/migrate_sqlite_to_postgres.py \
    --sqlite ./lobster.db \
    --postgres postgresql+psycopg://lobster:pass@127.0.0.1:5432/lobster \
    --create-tables --drop-target
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, delete, event, func, inspect, select, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import Base  # noqa: E402
from backend.app import models  # noqa: F401,E402  ensure ORM models are registered


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _is_sqlite_url(value: str) -> bool:
    return "sqlite" in (value or "").lower()


def _is_postgres_url(value: str) -> bool:
    lowered = (value or "").lower()
    return lowered.startswith("postgresql") or lowered.startswith("postgres://")


def _jsonify(value: Any) -> Any:
    # SQLAlchemy JSON values from SQLite are normally already decoded when using ORM metadata.
    return value


def _table_order() -> list:
    return list(Base.metadata.sorted_tables)


def _table_count(engine: Engine, table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)


def _backup_sqlite(sqlite_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"{sqlite_path.name}.{ts}.bak"
    with sqlite3.connect(sqlite_path) as src, sqlite3.connect(dst) as target:
        src.backup(target)
    return dst


def _truncate_target(engine: Engine, tables: list) -> None:
    with engine.begin() as conn:
        for table in reversed(tables):
            conn.execute(delete(table))


def _drop_target(engine: Engine) -> None:
    Base.metadata.drop_all(bind=engine)


def _copy_table(source: Engine, target: Engine, table, batch_size: int) -> int:
    source_cols = {c["name"] for c in inspect(source).get_columns(table.name)}
    columns = [col for col in table.columns if col.name in source_cols]
    if not columns:
        return 0
    select_stmt = select(*columns).select_from(table)
    copied = 0
    batch: list[dict[str, Any]] = []
    with source.connect() as src, target.begin() as dst:
        for row in src.execute(select_stmt).mappings():
            item = {key: _jsonify(value) for key, value in dict(row).items()}
            batch.append(item)
            if len(batch) >= batch_size:
                dst.execute(table.insert(), batch)
                copied += len(batch)
                batch.clear()
        if batch:
            dst.execute(table.insert(), batch)
            copied += len(batch)
    return copied


def _reset_postgres_sequences(engine: Engine, tables: list) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in tables:
            pk_cols = []
            for column in table.primary_key.columns:
                try:
                    python_type = column.type.python_type
                except NotImplementedError:
                    continue
                if python_type is int:
                    pk_cols.append(column)
            if len(pk_cols) != 1:
                continue
            pk = pk_cols[0]
            seq_name = conn.execute(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": pk.name},
            ).scalar()
            if not seq_name:
                continue
            conn.execute(
                text(
                    "SELECT setval(CAST(:seq_name AS regclass), "
                    "COALESCE((SELECT MAX(\"%s\") FROM \"%s\"), 0) + 1, false)"
                    % (pk.name, table.name)
                ),
                {"seq_name": seq_name},
            )


def _verify_counts(source: Engine, target: Engine, tables: list) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    for table in tables:
        src_exists = inspect(source).has_table(table.name)
        dst_exists = inspect(target).has_table(table.name)
        src_count = _table_count(source, table) if src_exists else 0
        dst_count = _table_count(target, table) if dst_exists else 0
        rows.append((table.name, src_count, dst_count))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate lobster_server SQLite data to PostgreSQL")
    parser.add_argument("--sqlite", default="lobster.db", help="SQLite database path, default: lobster.db")
    parser.add_argument("--postgres", required=True, help="Target SQLAlchemy PostgreSQL URL")
    parser.add_argument("--create-tables", action="store_true", help="Create ORM tables on PostgreSQL before copying")
    parser.add_argument("--drop-target", action="store_true", help="Drop ORM tables on PostgreSQL before copying")
    parser.add_argument("--truncate-target", action="store_true", help="Delete target table rows before copying")
    parser.add_argument("--backup-dir", default="db_backups", help="Directory for source SQLite backup")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="Only print counts and planned actions")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")
    if not _is_postgres_url(args.postgres):
        raise SystemExit("--postgres must be a PostgreSQL SQLAlchemy URL")
    if _is_sqlite_url(args.postgres):
        raise SystemExit("--postgres points to SQLite; refusing")
    if args.drop_target and not args.create_tables:
        raise SystemExit("--drop-target requires --create-tables")

    target = create_engine(args.postgres, pool_pre_ping=True)
    tables = _table_order()

    print(f"[migrate] source={sqlite_path.resolve()}")
    print(f"[migrate] target={target.url.render_as_string(hide_password=True)}")
    print(f"[migrate] tables={len(tables)}")
    backup = _backup_sqlite(sqlite_path, Path(args.backup_dir))
    print(f"[migrate] sqlite backup={backup}")
    source = create_engine(_sqlite_url(backup), connect_args={"check_same_thread": False})

    if args.dry_run:
        for name, src, dst in _verify_counts(source, target, tables):
            print(f"[dry-run] {name}: sqlite={src} postgres={dst}")
        return 0

    if args.drop_target:
        print("[migrate] dropping target ORM tables...")
        _drop_target(target)

    if args.create_tables:
        print("[migrate] creating target tables if missing...")
        Base.metadata.create_all(bind=target)

    if args.truncate_target:
        print("[migrate] truncating target tables...")
        _truncate_target(target, tables)

    for table in tables:
        if not inspect(source).has_table(table.name):
            print(f"[skip] {table.name}: missing in sqlite")
            continue
        copied = _copy_table(source, target, table, max(1, args.batch_size))
        print(f"[copy] {table.name}: {copied}")

    _reset_postgres_sequences(target, tables)

    print("[verify] row counts")
    mismatches = []
    for name, src, dst in _verify_counts(source, target, tables):
        mark = "OK" if src == dst else "MISMATCH"
        print(f"[verify] {mark} {name}: sqlite={src} postgres={dst}")
        if src != dst:
            mismatches.append((name, src, dst))
    if mismatches:
        print("[migrate] completed with count mismatches; do not cut over yet", file=sys.stderr)
        return 2
    print("[migrate] completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
