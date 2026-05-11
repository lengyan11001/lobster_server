"""
HiFly 公共声音预览预取脚本
============================

用法:
    1. 在 .env 里配置 HIFLY_CONSUMER_JWT=<从 hifly.cc 抓的 Bearer 后面那串>
    2. 把 hifly.cc 网站请求 /api/app/v1/tts_voice_groups 时返回的完整 JSON
       保存到 backend/lobster_server/data/hifly_public_voices_seed.json
    3. 在 lobster_server 根目录运行: python scripts/prefetch_hifly_previews.py
    4. 脚本会:
       - 读 seed (优先) 或调用 live API 拿声音列表
       - 对每个声音的数字 id 调消费者 preview 接口拿 base64 音频
       - 解码保存为 lobster_online/static/hifly_previews/{numeric_id}.wav
       - 生成 manifest.json 供后端 voice/library 端点读取

环境变量 (可选覆盖):
    HIFLY_CONSUMER_JWT          消费者站 JWT (Bearer xxx 中的 xxx 部分)
    HIFLY_PREVIEW_OUTPUT_DIR    wav 输出目录, 默认 ../../lobster_online/static/hifly_previews
    HIFLY_PREVIEW_SEED_FILE     seed JSON 路径, 默认 data/hifly_public_voices_seed.json
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# 允许从 backend 目录解析 settings (可选)
SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = SERVER_ROOT.parent.parent
sys.path.insert(0, str(SERVER_ROOT))

try:
    from backend.app.core.config import settings  # type: ignore
    DEFAULT_JWT = getattr(settings, "hifly_consumer_jwt", "") or ""
except Exception:
    settings = None
    DEFAULT_JWT = ""


CONSUMER_BASE = "https://hiflyworks-api.lingverse.co"
LIST_PATH = "/api/app/v1/tts_voice_groups"
PREVIEW_PATH = "/api/app/v1/tts_voices/{vid}/preview"
DEFAULT_PREVIEW_TEXT = "现在的一切都是为将来的梦想编织翅膀，让梦想在现实中展翅高飞。"
DEFAULT_PCM_SAMPLE_RATE = 16000


def consumer_headers(jwt: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {jwt}",
        "Origin": "https://hifly.cc",
        "Referer": "https://hifly.cc/",
        "x-client-type": "web",
        "x-lvs-language": "zh-CN",
        "x-name": "hiflyworks-web",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/147.0.0.0",
        "accept": "application/json, text/plain, */*",
    }


def load_seed_groups(seed_path: Path) -> List[Dict[str, Any]]:
    """从 seed JSON 读 voice groups。结构: {data: {list: [...]}}"""
    if not seed_path.exists():
        return []
    try:
        raw = json.loads(seed_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(f"[seed] 解析失败 {seed_path}: {exc}")
        return []
    data = raw.get("data") or raw
    if isinstance(data, dict):
        groups = data.get("list") or data.get("items") or []
    elif isinstance(data, list):
        groups = data
    else:
        groups = []
    return [g for g in groups if isinstance(g, dict)]


def fetch_live_groups(jwt: str, page_size: int = 100) -> List[Dict[str, Any]]:
    """实时拉取 (备选), 但通常需要正确的 cookie/header, 实战用 seed 更稳"""
    if not jwt:
        return []
    all_groups: List[Dict[str, Any]] = []
    seen_ids = set()
    for page in range(0, 20):
        try:
            r = requests.get(
                CONSUMER_BASE + LIST_PATH,
                headers=consumer_headers(jwt),
                params={"page": page, "size": page_size},
                timeout=20,
            )
            j = r.json()
        except Exception as exc:
            print(f"[live] page={page} 失败: {exc}")
            break
        items = (j.get("data") or {}).get("list") or []
        new_items = [g for g in items if isinstance(g, dict) and g.get("id") not in seen_ids]
        if not new_items:
            break
        for g in new_items:
            seen_ids.add(g.get("id"))
        all_groups.extend(new_items)
        if len(items) < page_size:
            break
    return all_groups


def fetch_preview(jwt: str, numeric_id: int, text: str) -> Optional[bytes]:
    url = CONSUMER_BASE + PREVIEW_PATH.format(vid=numeric_id)
    try:
        r = requests.post(
            url,
            headers={**consumer_headers(jwt), "Content-Type": "application/json"},
            json={"text": text},
            timeout=30,
        )
    except Exception as exc:
        print(f"  ! preview {numeric_id} 网络错误: {exc}")
        return None
    if r.status_code != 200:
        print(f"  ! preview {numeric_id} HTTP {r.status_code}: {r.text[:120]}")
        return None
    try:
        j = r.json()
    except Exception:
        print(f"  ! preview {numeric_id} 非 JSON: {r.text[:120]}")
        return None
    if j.get("code") != 0:
        print(f"  ! preview {numeric_id} biz code={j.get('code')} msg={j.get('message')}")
        return None
    audio_b64 = ((j.get("data") or {}).get("audio_base64")) or ""
    if not audio_b64:
        return None
    try:
        return base64.b64decode(audio_b64)
    except Exception as exc:
        print(f"  ! preview {numeric_id} base64 解码失败: {exc}")
        return None


def ensure_wav_bytes(audio: bytes) -> bytes:
    if audio.startswith(b"RIFF"):
        return audio
    wav_path = None
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(DEFAULT_PCM_SAMPLE_RATE)
        wf.writeframes(audio)
    return buf.getvalue()


def main():
    jwt = os.environ.get("HIFLY_CONSUMER_JWT") or DEFAULT_JWT
    if not jwt:
        print("[FATAL] 没有 JWT。请在 .env 设置 HIFLY_CONSUMER_JWT 或 export 环境变量")
        sys.exit(1)

    seed_path = Path(os.environ.get("HIFLY_PREVIEW_SEED_FILE") or
                     SERVER_ROOT / "data" / "hifly_public_voices_seed.json")
    out_dir = Path(os.environ.get("HIFLY_PREVIEW_OUTPUT_DIR") or
                   PROJECT_ROOT / "lobster_online" / "static" / "hifly_previews")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[init] seed = {seed_path}")
    print(f"[init] out  = {out_dir}")

    groups = load_seed_groups(seed_path)
    if len(groups) < 5:
        print(f"[seed] 只有 {len(groups)} 条, 尝试 live API...")
        live = fetch_live_groups(jwt)
        if len(live) > len(groups):
            groups = live
            print(f"[live] 拿到 {len(groups)} 个 group")

    if not groups:
        print("[FATAL] 没有 voice groups 数据。把 hifly.cc 浏览器响应保存到 seed 文件后重跑")
        sys.exit(2)

    print(f"[run] 共 {len(groups)} 个 voice group, 开始预取...")

    manifest_groups: List[Dict[str, Any]] = []
    skip_ok = 0
    fetched = 0
    failed = 0

    for gi, grp in enumerate(groups, 1):
        gid = grp.get("id")
        gtitle = grp.get("title") or ""
        cover = grp.get("cover_url") or ""
        members = grp.get("members") or []
        manifest_members: List[Dict[str, Any]] = []

        for m in members:
            mid = m.get("id")
            if not isinstance(mid, int):
                continue
            mtitle = m.get("title") or gtitle
            voice_name = m.get("voice_name") or ""
            preview_text = m.get("preview_text") or DEFAULT_PREVIEW_TEXT
            tts_level = m.get("tts_level", 10)

            wav_path = out_dir / f"{mid}.wav"
            preview_url_rel = f"/static/hifly_previews/{mid}.wav"

            if wav_path.exists() and wav_path.stat().st_size > 1024:
                skip_ok += 1
                manifest_members.append({
                    "id": mid, "title": mtitle, "voice_name": voice_name,
                    "preview_url": preview_url_rel, "preview_text": preview_text,
                    "tts_level": tts_level,
                })
                continue

            print(f"[{gi}/{len(groups)}] preview {mid} ({mtitle})", flush=True)
            audio = fetch_preview(jwt, mid, preview_text)
            if audio:
                wav_path.write_bytes(ensure_wav_bytes(audio))
                fetched += 1
                manifest_members.append({
                    "id": mid, "title": mtitle, "voice_name": voice_name,
                    "preview_url": preview_url_rel, "preview_text": preview_text,
                    "tts_level": tts_level,
                })
                # 礼貌的限速
                time.sleep(0.2)
            else:
                failed += 1
                manifest_members.append({
                    "id": mid, "title": mtitle, "voice_name": voice_name,
                    "preview_url": "", "preview_text": preview_text,
                    "tts_level": tts_level,
                })

        if manifest_members:
            manifest_groups.append({
                "id": gid, "title": gtitle, "cover_url": cover,
                "members": manifest_members,
            })

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"groups": manifest_groups, "generated_at": int(time.time())},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    print()
    print(f"[done] 已存在跳过: {skip_ok}, 新拉取: {fetched}, 失败: {failed}")
    print(f"[done] manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
