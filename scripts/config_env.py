#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键配置 .env（lobster_server 服务器仓库）。
在服务器上运行：python3 scripts/config_env.py
- 交互：按提示输入，回车保留原值或跳过。
- 一键：先 export CONFIG_WECHAT_APP_ID=xxx CONFIG_WECHAT_APP_SECRET=xxx ... 再运行，将 CONFIG_* 写入 .env。
应用只读 .env 里的 WECHAT_APP_ID 等，不读 CONFIG_*，所以必须用本脚本把 CONFIG_* 写入 .env。
"""
import os
import re
import shutil
import sys
from pathlib import Path

# 项目根目录（本脚本在 scripts/config_env.py，根目录为上级的上级）
ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"

# 可配置项：(.env 中的 key, 环境变量名 CONFIG_*, 交互提示, 是否可留空)
CONFIG_ITEMS = [
    ("WECHAT_APP_ID", "CONFIG_WECHAT_APP_ID", "微信登录 AppID（小程序ID）", True),
    ("WECHAT_APP_SECRET", "CONFIG_WECHAT_APP_SECRET", "微信登录 AppSecret（小程序密钥）", True),
    ("WECHAT_MCH_ID", "CONFIG_WECHAT_MCH_ID", "微信支付 商户号", True),
    ("WECHAT_PAY_APIV3_KEY", "CONFIG_WECHAT_PAY_APIV3_KEY", "微信支付 APIv3 密钥（32位）", True),
    ("WECHAT_PAY_SERIAL_NO", "CONFIG_WECHAT_PAY_SERIAL_NO", "微信支付 证书序列号", True),
    ("WECHAT_PAY_PRIVATE_KEY_PATH", "CONFIG_WECHAT_PAY_PRIVATE_KEY_PATH", "微信支付 商户私钥 .pem 路径", True),
    ("WECHAT_PAY_PUBLIC_KEY_PATH", "CONFIG_WECHAT_PAY_PUBLIC_KEY_PATH", "微信支付公钥模式 公钥文件路径（商户平台-API安全-微信支付公钥-下载）", True),
    ("WECHAT_PAY_PUBLIC_KEY_ID", "CONFIG_WECHAT_PAY_PUBLIC_KEY_ID", "微信支付公钥ID（截图公钥ID，如 PUB_KEY_ID_01...）", True),
    ("SUTUI_SERVER_TOKEN", "CONFIG_SUTUI_SERVER_TOKEN", "速推 服务器 Token（做视频能力必填）", True),
    ("PUBLIC_BASE_URL", "CONFIG_PUBLIC_BASE_URL", "回调根地址（服务器填 http://公网IP:8000，本地可留空）", True),
    ("CAPABILITY_SUTUI_MCP_URL", "CONFIG_CAPABILITY_SUTUI_MCP_URL", "速推 MCP 地址（默认留空用官方）", True),
]


def read_current_env():
    """解析现有 .env，返回 KEY -> value 的映射。"""
    out = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(?:#\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            out[key] = val
    return out


def gather_values_from_env():
    """从环境变量 CONFIG_* 读取要写入 .env 的值（注意：export KEY= 后若有空格会一并读入，此处已 strip 去掉首尾空格）。"""
    values = {}
    for key, env_key, _label, _allow_empty in CONFIG_ITEMS:
        v = (os.environ.get(env_key, "") or "").strip()
        if v:
            values[key] = v
    return values


def gather_values_interactive(current):
    """交互式询问每一项。"""
    values = {}
    for key, _env_key, label, allow_empty in CONFIG_ITEMS:
        cur = current.get(key, "")
        if cur and not cur.startswith("#"):
            hint = f" [当前: {cur[:20]}{'...' if len(cur) > 20 else ''}]"
        else:
            hint = " [留空跳过]"
        try:
            raw = input(f"{label}{hint}\n> ").strip()
        except EOFError:
            break
        if raw:
            values[key] = raw
        elif cur and not cur.startswith("#"):
            values[key] = cur
        elif not allow_empty:
            pass
    return values


def update_env_file(updates):
    """把 updates 写回 .env。"""
    if not updates:
        return
    if not ENV_PATH.exists():
        if EXAMPLE_PATH.exists():
            shutil.copy(EXAMPLE_PATH, ENV_PATH)
        else:
            ENV_PATH.write_text("", encoding="utf-8")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    keys_written = set()

    for line in lines:
        m = re.match(r"^(\s*#?\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if m and m.group(2) in updates:
            key = m.group(2)
            val = (updates[key] or "").strip()
            keys_written.add(key)
            if "\n" in val or " " in val or val.startswith("#"):
                out.append(f'{key}="{val}"')
            else:
                out.append(f"{key}={val}")
            continue
        out.append(line)

    for key, val in updates.items():
        if key not in keys_written:
            val = (val or "").strip()
            if "\n" in val or " " in val or val.startswith("#"):
                out.append(f'{key}="{val}"')
            else:
                out.append(f"{key}={val}")

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    os.chdir(ROOT)
    if not EXAMPLE_PATH.exists():
        print("未找到 .env.example，请在 lobster_server 项目根目录执行本脚本。", file=sys.stderr)
        sys.exit(1)

    if not ENV_PATH.exists():
        shutil.copy(EXAMPLE_PATH, ENV_PATH)
        print("已从 .env.example 复制生成 .env")

    current = read_current_env()
    use_env = any(os.environ.get(env_key) for _k, env_key, *_ in CONFIG_ITEMS)

    if use_env:
        updates = gather_values_from_env()
        print("从环境变量 CONFIG_* 读取到", len(updates), "项，正在写入 .env ...")
    else:
        print("未检测到 CONFIG_* 环境变量，进入交互配置（直接回车保留原值或跳过）。\n")
        updates = gather_values_interactive(current)

    if updates:
        update_env_file(updates)
        print("已更新 .env：", ", ".join(updates.keys()))
    else:
        print("无新配置写入。")


if __name__ == "__main__":
    main()
