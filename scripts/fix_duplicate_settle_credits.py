#!/usr/bin/env python3
"""冲正“数字人终态补偿重试”导致的重复结算退差（默认 dry-run）。

背景（2026-11-10 ~ 2026-11-23）：
  record-call 在账务提交后才因 NameError(idem_key) 返回 500；
  shanjian 的终态补偿每约 6 分钟重试一次，每次都成功写一条 settle 退差，
  但任务状态一直停在 settle_failed，于是无限循环。

本脚本把同一 (user_id, task_id) 的第 2 条及之后的 settle 退差冲正。
必须先上线 record-call 修复，否则循环会继续新增重复退差。

用法：
  PYTHONPATH=/opt/lobster-server .venv/bin/python scripts/fix_duplicate_settle_credits.py
  PYTHONPATH=/opt/lobster-server .venv/bin/python scripts/fix_duplicate_settle_credits.py --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.app.db import SessionLocal  # noqa: E402
from backend.app.models import User  # noqa: E402
from backend.app.services.credit_ledger import append_credit_ledger  # noqa: E402
from backend.app.services.credits_amount import quantize_credits  # noqa: E402

DUP_SQL = """
WITH ranked AS (
    SELECT l.id, l.user_id, l.delta,
           c.request_payload->>'task_id' AS task_id,
           ROW_NUMBER() OVER (
               PARTITION BY l.user_id, c.request_payload->>'task_id'
               ORDER BY l.id
           ) AS rn
    FROM credit_ledger l
    JOIN capability_call_logs c ON l.ref_id = c.id::text
    WHERE l.entry_type = 'settle'
      AND c.source = 'shanjian_digital_human_video_task'
      AND c.request_payload->>'task_id' IS NOT NULL
      AND l.created_at >= :since
      {user_filter}
)
SELECT user_id,
       count(*) AS total_rows,
       count(*) FILTER (WHERE rn > 1) AS dup_rows,
       coalesce(sum(delta) FILTER (WHERE rn > 1), 0) AS dup_credits
FROM ranked
GROUP BY user_id
HAVING count(*) FILTER (WHERE rn > 1) > 0
ORDER BY dup_credits DESC
"""

ALREADY_FIXED_SQL = """
SELECT 1 FROM credit_ledger
WHERE user_id = :u AND entry_type = 'settle_reversal'
  AND meta->>'fix' = 'dup_settle_v1'
LIMIT 1
"""


def _fmt(v) -> str:
    try:
        return f"{float(v):,.4f}"
    except Exception:
        return str(v)


def _dup_rows(db, since: str, user_id: int | None):
    user_filter = "AND l.user_id = :uid" if user_id else ""
    sql = DUP_SQL.format(user_filter=user_filter)
    params = {"since": since}
    if user_id:
        params["uid"] = int(user_id)
    return db.execute(text(sql), params).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正改余额；缺省只 dry-run")
    ap.add_argument("--since", default="2026-11-01", help="只处理该时间之后的 settle")
    ap.add_argument("--user-id", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个用户")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = _dup_rows(db, args.since, args.user_id)
        if args.limit:
            rows = rows[: args.limit]
        if not rows:
            print("没有发现重复 settle。")
            return 0

        total_dup_rows = sum(int(r[2]) for r in rows)
        total_dup_credits = sum(Decimal(str(r[3])) for r in rows)
        print(f"模式: {'APPLY' if args.apply else 'DRY-RUN'}")
        print(
            f"since={args.since} users={len(rows)} "
            f"dup_rows={total_dup_rows} dup_credits={_fmt(total_dup_credits)}"
        )
        print("注意：必须先上线 record-call 修复，否则补偿循环会继续新增重复退差。")
        print("-" * 108)

        applied_total = Decimal("0")
        shortfall_total = Decimal("0")
        skipped = 0
        for r in rows:
            uid = int(r[0])
            total_rows, dup_rows = int(r[1]), int(r[2])
            dup_credits = Decimal(str(r[3]))
            q = db.query(User).filter(User.id == uid)
            if args.apply:
                q = q.with_for_update()
            user = q.first()
            if user is None:
                print(f"uid={uid} 用户不存在，跳过")
                continue
            already = db.execute(text(ALREADY_FIXED_SQL), {"u": uid}).first()
            credits = quantize_credits(user.credits)
            applied = min(credits, dup_credits)
            shortfall = dup_credits - applied
            print(
                f"uid={uid:<5} settle={total_rows:<6} dup={dup_rows:<6} "
                f"dup_credits={_fmt(dup_credits):>14} current={_fmt(credits):>14} "
                f"-> {_fmt(credits - applied):>14} shortfall={_fmt(shortfall):>12}"
                + ("  [已冲正，跳过]" if already else "")
            )
            if not args.apply or already:
                skipped += 1 if already else 0
                continue
            if applied <= 0:
                continue
            new_bal = quantize_credits(credits - applied)
            user.credits = new_bal
            append_credit_ledger(
                db,
                uid,
                -applied,
                "settle_reversal",
                new_bal,
                description="重复结算退差冲正",
                ref_type="ops_fix",
                meta={
                    "fix": "dup_settle_v1",
                    "reason": "record_call_500_retry_loop",
                    "dup_rows": dup_rows,
                    "dup_credits": str(dup_credits),
                    "applied": str(applied),
                    "shortfall": str(shortfall),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                },
            )
            applied_total += applied
            shortfall_total += shortfall
        print("-" * 108)
        if args.apply:
            db.commit()
            print(
                f"已冲正 {_fmt(applied_total)} 分；余额不足未冲 {_fmt(shortfall_total)} 分"
                f"（已记入 meta.shortfall）；跳过已冲正用户 {skipped} 个"
            )
        else:
            db.rollback()
            print("dry-run 结束，未改任何数据。确认后加 --apply。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
