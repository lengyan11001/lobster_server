"""给 canvas_project 回填分类字段。

用法：
    python -m scripts.backfill_canvas_category            # 只统计
    python -m scripts.backfill_canvas_category --apply    # 写库（只填空值）
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

from sqlalchemy import inspect, text

from backend.app.api.canvas_hub import classify_template_category
from backend.app.db import SessionLocal


def main() -> None:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    insp = inspect(db.get_bind())
    has_col = "category" in {c["name"] for c in insp.get_columns("canvas_project")}
    rows = db.execute(text("select id, name, coalesce(description, '') from canvas_project order by id")).fetchall()
    buckets = defaultdict(list)
    for _rid, name, descr in rows:
        buckets[classify_template_category(name, descr)].append(name)
    print("total", len(rows), dict(Counter({k: len(v) for k, v in buckets.items()})))
    for key in ("poster", "xhs", "portrait", "cartoon", "other"):
        print("[" + key + "]", len(buckets[key]))
    if not apply:
        return
    db.rollback()
    if not has_col:
        db.execute(text("ALTER TABLE canvas_project ADD COLUMN category VARCHAR(32) NOT NULL DEFAULT ''"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_canvas_project_category ON canvas_project (category)"))
        db.commit()
    for rid, name, descr in rows:
        db.execute(text("update canvas_project set category = :c where id = :i and coalesce(category, '') = ''"),
                   {"c": classify_template_category(name, descr), "i": rid})
    db.commit()
    print("updated", len(rows))
    db.close()


if __name__ == "__main__":
    main()
