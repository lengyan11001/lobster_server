"""飞书凭证自检 + 试发（建完自建应用后第一时间跑这个）。

用法（在仓库根目录；先在 .env 或环境变量里配好）：
  # 1) 只验凭证 + 邮箱换 open_id
  python scripts/feishu_check.py --email linke@bhzn.top

  # 2) 给这个人私聊发一条测试卡片
  python scripts/feishu_check.py --email linke@bhzn.top --send

  # 3) 只测群机器人 webhook
  python scripts/feishu_check.py --webhook https://open.feishu.cn/open-apis/bot/v2/hook/xxx --send

环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET     企业自建应用（发单聊必需）
  MANAGE_FEISHU_WEBHOOK                 群机器人 webhook（发群用）
  FEISHU_API_BASE                       可选，私有化/自测覆盖（默认 open.feishu.cn/open-apis）

常见错误码：
  10003 / 10014  app_id 或 app_secret 不对（或不是这个企业的应用）
  99991672       权限没开：去开发者后台「权限管理」勾 im:message（+ 用邮箱换 id 的通讯录权限）后重新发版
  99991663      应用没发布/没被管理员审批通过，或可用范围不含该员工
  230002 / 230013 员工不在应用可用范围内
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

from dotenv import load_dotenv

load_dotenv(os.path.join(_root, ".env"), override=False)

from backend.app.services import feishu_bot  # noqa: E402

HINTS = {
    "10003": "app_id 不存在或不是这个企业的应用（确认用的是本企业账号建的应用）",
    "10014": "app_secret 不正确",
    "99991672": "权限未开通：开发者后台 → 权限管理 → 勾 im:message（+ contact:user.id:readonly）→ 创建版本并发布",
    "99991663": "应用未发布/未通过管理员审批，或可用范围不含该用户",
    "230002": "接收人不在应用可用范围内（开发者后台 → 版本与可用范围）",
    "230013": "接收人不在应用可用范围内",
}


def _hint(exc: Exception) -> str:
    text = str(exc)
    for code, msg in HINTS.items():
        if code in text:
            return "  → " + msg
    return ""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="", help="要查/要发的人的企业邮箱")
    ap.add_argument("--open-id", default="", help="已知 open_id（可替代 --email）")
    ap.add_argument("--webhook", default="", help="群机器人 webhook（不填则用 MANAGE_FEISHU_WEBHOOK）")
    ap.add_argument("--send", action="store_true", help="真的发一条测试卡片（默认只检查）")
    ap.add_argument("--title", default="飞书接入自检", help="测试卡片标题")
    args = ap.parse_args()

    print("== 配置 ==")
    print("FEISHU_APP_ID       :", (feishu_bot._env("FEISHU_APP_ID") or "(未配置)")[:12])
    print("FEISHU_APP_SECRET   :", "(已配置)" if feishu_bot._env("FEISHU_APP_SECRET") else "(未配置)")
    print("群 webhook          :", (feishu_bot.webhook_url() or args.webhook or "(未配置)")[:52])
    print("API base            :", feishu_bot.api_base())

    ok = True
    receive_id, id_type = args.open_id, "open_id"

    if feishu_bot.app_enabled():
        print("\n== 1) tenant_access_token ==")
        try:
            token = await feishu_bot.fetch_tenant_access_token()
            print("OK  拿到 token:", token[:10] + "…")
        except Exception as exc:
            ok = False
            print("FAIL", exc)
            print(_hint(exc))
    else:
        print("\n== 1) tenant_access_token ==\nSKIP 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    if args.email and feishu_bot.app_enabled():
        print("\n== 2) 邮箱换 open_id:", args.email, "==")
        try:
            oid = await feishu_bot.resolve_open_id(args.email)
            if oid:
                receive_id, id_type = oid, "open_id"
                print("OK  open_id:", oid)
            else:
                ok = False
                print("FAIL 没查到这个邮箱")
                print("  → 确认这个邮箱是对方飞书账号的邮箱、且企业通讯录里可见（权限 contact:user.id:readonly）")
        except Exception as exc:
            ok = False
            print("FAIL", exc)
            print(_hint(exc))

    if args.send and feishu_bot.app_enabled() and receive_id:
        print("\n== 3) 私聊发测试卡片 ->", receive_id, "==")
        card = feishu_bot.build_card(args.title, [
            "这是一条来自 manage.bhzn.top 的自检消息。",
            "收到说明：自建应用 + im:message 已通。",
        ], link="https://manage.bhzn.top", link_text="打开管理台")
        try:
            res = await feishu_bot.send_card_to_user(receive_id, card, receive_id_type=id_type)
            print("OK  已发送:", res.get("data") or res)
        except Exception as exc:
            ok = False
            print("FAIL", exc)
            print(_hint(exc))

    if args.send and (args.webhook or feishu_bot.webhook_url()):
        print("\n== 4) 群机器人 webhook 发测试卡片 ==")
        try:
            res = await feishu_bot.send_card(args.title + " · 群测试", ["群机器人链路自检。"],
                                             webhook=args.webhook or feishu_bot.webhook_url())
            print("OK  ", res.get("response") or res)
        except Exception as exc:
            ok = False
            print("FAIL", exc)
            print(_hint(exc))

    print("\n== 结论 ==", "全部通过 ✅" if ok else "有失败项 ❌（按上面 → 的提示去开放平台改）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
