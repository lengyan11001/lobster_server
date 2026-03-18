#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在服务器上运行，用当前 .env 的微信支付参数请求 GET /v3/certificates，
打印微信返回的状态码和响应体，便于排查 401/404。
用法：cd /root/lobster_server && .venv/bin/python scripts/wechat_fetch_platform_cert.py
"""
import base64
import os
import random
import string
import sys
import time
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 从 .env 加载
def load_env():
    env = {}
    p = ROOT / ".env"
    if not p.exists():
        print("No .env found", file=sys.stderr)
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def main():
    env = load_env()
    mch_id = env.get("WECHAT_MCH_ID", "").strip()
    serial_no = env.get("WECHAT_PAY_SERIAL_NO", "").strip()
    apiv3_key = (env.get("WECHAT_PAY_APIV3_KEY", "") or "").strip()[:32]
    key_path = env.get("WECHAT_PAY_PRIVATE_KEY_PATH", "").strip()
    if not key_path or not Path(key_path).is_absolute():
        key_path = str(ROOT / key_path)
    if not all([mch_id, serial_no, apiv3_key, key_path]):
        print("Missing WECHAT_MCH_ID / WECHAT_PAY_SERIAL_NO / WECHAT_PAY_APIV3_KEY / WECHAT_PAY_PRIVATE_KEY_PATH in .env")
        return 1
    try:
        private_key_pem = Path(key_path).read_text(encoding="utf-8")
    except Exception as e:
        print("Read private key failed:", e)
        return 1

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    import urllib3
    urllib3.disable_warnings()
    import requests

    timestamp = str(int(time.time()))
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    url = "https://api.mch.weixin.qq.com/v3/certificates"
    path = "/v3/certificates"
    sign_str = "GET\n%s\n%s\n%s\n\n" % (path, timestamp, nonce)

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(sign_str.encode(), padding.PKCS1v15(), hashes.SHA256())
    signature = base64.b64encode(sig).decode()

    auth = (
        'WECHATPAY2-SHA256-RSA2048 mchid="%s",nonce_str="%s",signature="%s",timestamp="%s",serial_no="%s"'
        % (mch_id, nonce, signature, timestamp, serial_no)
    )
    r = requests.get(url, headers={"Authorization": auth, "Accept": "application/json"}, timeout=10)
    print("Status:", r.status_code)
    print("Body:", r.text[:500] if r.text else "(empty)")
    if r.status_code == 200 and r.text:
        try:
            data = r.json()
            print("data keys:", list(data.keys()) if isinstance(data, dict) else type(data))
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
