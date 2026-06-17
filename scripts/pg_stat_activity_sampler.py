#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


SQL = r"""
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select
        pid,
        usename,
        application_name,
        client_addr::text as client_addr,
        state,
        wait_event_type,
        wait_event,
        extract(epoch from (now() - xact_start))::int as xact_age_s,
        extract(epoch from (now() - query_start))::int as query_age_s,
        extract(epoch from (now() - state_change))::int as state_age_s,
        left(regexp_replace(query, E'[\\n\\r\\t]+', ' ', 'g'), 500) as query
    from pg_stat_activity
    where datname = current_database()
    order by
        case when state = 'active' then 0 when state = 'idle in transaction' then 1 else 2 end,
        coalesce(xact_start, query_start, state_change) nulls last,
        pid
) t;
"""


def _database_url() -> str:
    value = (os.environ.get("DATABASE_URL") or "").strip()
    if value:
        return _psql_url(value)
    env_path = Path(os.environ.get("LOBSTER_ENV_FILE") or "/opt/lobster-server/.env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                return _psql_url(raw.strip().strip('"').strip("'"))
    raise RuntimeError("DATABASE_URL is not configured")


def _psql_url(value: str) -> str:
    return (
        value
        .replace("postgresql+psycopg2://", "postgresql://", 1)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )


def _safe_error(exc: Exception, database_url: str) -> str:
    text = str(exc)
    if database_url:
        text = text.replace(database_url, "<DATABASE_URL>")
    return text[:1000]


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def _sample(database_url: str) -> dict:
    result = subprocess.run(
        ["psql", database_url, "-Atq", "-c", SQL],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    rows = json.loads((result.stdout or "[]").strip() or "[]")
    summary: dict[str, int] = {}
    for row in rows:
        state = row.get("state") or "none"
        summary[state] = summary.get(state, 0) + 1
    return {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "summary": summary,
        "connections": rows,
    }


def main() -> None:
    interval = _int_env("PG_ACTIVITY_SAMPLE_INTERVAL", 2)
    log_path = Path(os.environ.get("PG_ACTIVITY_LOG") or "/opt/lobster-server/logs/pg-stat-activity.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    database_url = _database_url()
    while True:
        try:
            payload = _sample(database_url)
        except Exception as exc:
            payload = {
                "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                "error": type(exc).__name__,
                "detail": _safe_error(exc, database_url),
            }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        time.sleep(interval)


if __name__ == "__main__":
    main()
