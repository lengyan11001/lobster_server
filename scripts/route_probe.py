"""能力路由探针：用真实模型验证「用户这句话会选哪个能力」。

用法（在服务器上）：
    python -m scripts.route_probe
    python -m scripts.route_probe --brief-only
"""
from __future__ import annotations

import argparse
import asyncio
import re
from typing import List, Tuple

from backend.app.services.mastra_online_capabilities import mastra_online_capabilities
from backend.app.services.plan.llm import complete

CASES: List[Tuple[str, str]] = [
    ("今天有什么热搜", "online.discover_hotlist"),
    ("最近 AI 行业有什么新闻", "online.discover_news"),
    ("未来一个月有什么节日可以蹭", "online.discover_calendar"),
    ("家居收纳有什么蓝海选题", "online.discover_gap"),
    ("这个选题值不值得做", "plan_topic_eval"),
    ("帮我写 6 个开头钩子", "plan_hook"),
    ("这个月内容怎么排", "plan_calendar"),
    ("我的定位是什么，怎么和竞品区分", "plan_positioning"),
    ("拆解一下这个竞品账号", "online.discover_competitor"),
    ("把这条内容发到小红书", "online.publish_content"),
    ("生成一条数字人口播视频", "online.digital_human_v2"),
    ("打开这个网页看看正文", "online.browser_extract_page"),
]

SYSTEM = (
    "你在做能力路由。只从给定能力里选最合适的一个，只输出能力 id，不要解释。"
    "如果能力列表里没有合适的，输出 NEED_INDEX。"
)


def catalog_text() -> str:
    caps = mastra_online_capabilities()
    lines = []
    for cid, cfg in caps.items():
        name = str((cfg or {}).get("name") or "")
        desc = str((cfg or {}).get("description") or "")[:120]
        lines.append(f"- {cid}：{name}｜{desc}")
    return "\n".join(lines)


def brief_text() -> str:
    from backend.app.services.work_mode_brief import _online_lines

    return "\n".join(_online_lines(""))


async def ask(message: str, catalog: str, brief_only: bool) -> str:
    body = ("能力列表：\n" + brief_text() + "\n\n用户消息：" + message) if brief_only else (
        "能力列表：\n" + catalog + "\n\n用户消息：" + message)
    text = await complete(SYSTEM, body, temperature=0.0, max_tokens=32, attempts=2)
    m = re.search(r"(online\.[a-z0-9_]+|plan_[a-z0-9_]+|NEED_INDEX)", text or "")
    return m.group(1) if m else text.strip()[:40]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief-only", action="store_true")
    args = ap.parse_args()
    catalog = catalog_text()
    ok = 0
    for message, expected in CASES:
        got = await ask(message, catalog, args.brief_only)
        hit = got == expected or (args.brief_only and expected.startswith("online.discover") and got == "NEED_INDEX")
        ok += 1 if hit else 0
        print(("OK  " if hit else "MISS") + f" | {message} | 期望 {expected} | 实得 {got}")
    print(f"\n准确率 {ok}/{len(CASES)}")


if __name__ == "__main__":
    asyncio.run(main())
