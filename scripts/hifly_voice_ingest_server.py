"""
HiFly 公共声音一键导入服务（浏览器全程代理版）
==========================================

工作流:
  1. 运行本脚本: python scripts/hifly_voice_ingest_server.py
     - 自动把粘贴用 JS 复制到剪贴板
     - 自动打开 https://hifly.cc/ 在默认浏览器
  2. 在 hifly.cc 已登录页面按 F12 -> Console
  3. Ctrl+V 回车 (剪贴板已是脚本)
  4. JS 会在已登录浏览器里:
     - 拉公共声音列表 (size=200)
     - 对每个 voice 调 preview 接口拿 base64 音频
     - 分批 POST 到本地服务
  5. 本服务把音频解码存为 wav，并写 manifest.json + seed.json
  6. 完成后自动退出。

整个过程除了 Ctrl+V + Enter 你不需要做任何事。
依赖: 仅 Python 标准库
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = SERVER_ROOT.parent.parent

PORT = 18765
HOST = "127.0.0.1"
SEED_PATH = SERVER_ROOT / "data" / "hifly_public_voices_seed.json"
OUT_DIR = PROJECT_ROOT / "lobster_online" / "static" / "hifly_previews"
MANIFEST_PATH = OUT_DIR / "manifest.json"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "https://hifly.cc",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
}

# 进度状态
state = {
    "groups": [],          # voice groups 列表
    "expected_voices": 0,  # 期望要收的预览数
    "received_voices": 0,  # 已收到的
    "failed_voices": 0,
    "list_received": False,
    "all_done": threading.Event(),
}


def _save_seed_and_manifest():
    """从 state['groups'] 写 seed.json 和 manifest.json (基于已存在 wav)"""
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(json.dumps(
        {"code": 0, "message": "OK",
         "data": {"list": state["groups"], "total": len(state["groups"])}},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_groups = []
    for grp in state["groups"]:
        gid = grp.get("id")
        gtitle = grp.get("title") or ""
        cover = grp.get("cover_url") or ""
        members_out = []
        for m in grp.get("members") or []:
            mid = m.get("id")
            if not isinstance(mid, int):
                continue
            wav_path = OUT_DIR / f"{mid}.wav"
            preview_url = f"/static/hifly_previews/{mid}.wav" if (
                wav_path.exists() and wav_path.stat().st_size > 1024
            ) else ""
            members_out.append({
                "id": mid,
                "title": m.get("title") or gtitle,
                "voice_name": m.get("voice_name") or "",
                "preview_url": preview_url,
                "preview_text": m.get("preview_text") or "",
                "tts_level": m.get("tts_level", 10),
            })
        if members_out:
            manifest_groups.append({
                "id": gid, "title": gtitle, "cover_url": cover,
                "members": members_out,
            })

    MANIFEST_PATH.write_text(json.dumps(
        {"groups": manifest_groups, "generated_at": int(time.time())},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")


class IngestHandler(BaseHTTPRequestHandler):
    def _cors(self):
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": True,
            "list_received": state["list_received"],
            "expected": state["expected_voices"],
            "received": state["received_voices"],
            "failed": state["failed_voices"],
        }).encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._json(400, {"ok": False, "error": f"json: {e}"})
            return

        if self.path == "/list":
            data = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(data, dict):
                groups = data.get("list") or []
            elif isinstance(data, list):
                groups = data
            else:
                groups = []
            groups = [g for g in groups if isinstance(g, dict)]
            if not groups:
                self._json(400, {"ok": False, "error": "empty groups"})
                return
            state["groups"] = groups
            expected = sum(
                1 for g in groups for m in (g.get("members") or [])
                if isinstance(m.get("id"), int)
            )
            state["expected_voices"] = expected
            state["list_received"] = True
            print(f"[ingest] groups={len(groups)} 期望预览数={expected}")
            self._json(200, {"ok": True, "expected": expected})
            return

        if self.path == "/audio":
            vid = payload.get("id")
            audio_b64 = payload.get("audio_base64") or ""
            ok = bool(payload.get("ok"))
            if not isinstance(vid, int):
                self._json(400, {"ok": False, "error": "bad id"})
                return
            if ok and audio_b64:
                try:
                    audio = base64.b64decode(audio_b64)
                    OUT_DIR.mkdir(parents=True, exist_ok=True)
                    (OUT_DIR / f"{vid}.wav").write_bytes(audio)
                    state["received_voices"] += 1
                    if state["received_voices"] % 10 == 0:
                        print(f"[ingest] 进度 {state['received_voices']}/{state['expected_voices']}")
                except Exception as e:
                    state["failed_voices"] += 1
                    print(f"[ingest] {vid} 解码失败: {e}")
            else:
                state["failed_voices"] += 1

            done = (state["received_voices"] + state["failed_voices"]) >= state["expected_voices"]
            self._json(200, {"ok": True, "done": done})
            if done and state["expected_voices"] > 0:
                state["all_done"].set()
            return

        if self.path == "/finish":
            # JS 主动通知结束（即便有 failure 也允许收尾）
            state["all_done"].set()
            self._json(200, {"ok": True})
            return

        self._json(404, {"ok": False, "error": "not found"})

    def log_message(self, format, *args):
        return


JS_SNIPPET = r"""
(async () => {
  const LOCAL = 'http://127.0.0.1:18765';
  const log = (...a) => console.log('[lobster-ingest]', ...a);

  // 1. 取最大 size 的列表
  let listBody = null;
  for (const size of [200, 100, 50]) {
    try {
      const r = await fetch(`/api/app/v1/tts_voice_groups?page=0&size=${size}`, { credentials: 'include' });
      const j = await r.json();
      const total = j?.data?.total ?? 0;
      log(`size=${size} total=${total}`);
      if (total > (listBody?.data?.total ?? 0)) listBody = j;
    } catch (e) { log('size', size, '失败', e); }
  }
  if (!listBody?.data?.list?.length) {
    alert('未取到列表。请确认已登录 hifly.cc 并刷新当前页后重试。');
    return;
  }

  // 2. 推送列表给本地服务
  const listResp = await fetch(LOCAL + '/list', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(listBody),
  }).then(r => r.json());
  log('本地接收列表:', listResp);
  if (!listResp.ok) {
    alert('本地服务拒收列表: ' + (listResp.error || '?'));
    return;
  }
  const expected = listResp.expected;
  log(`需要拉 ${expected} 个声音的预览…`);

  // 3. 串行拉每个 voice 的 preview，回传 base64
  const groups = listBody.data.list;
  const items = [];
  for (const g of groups) {
    for (const m of (g.members || [])) {
      if (typeof m.id === 'number') {
        items.push({ id: m.id, text: m.preview_text || '现在的一切都是为将来的梦想编织翅膀，让梦想在现实中展翅高飞。' });
      }
    }
  }
  log(`待处理 ${items.length} 项`);

  let ok = 0, fail = 0;
  // 串行 + 限速，避免触发风控
  for (const it of items) {
    try {
      const r = await fetch(`/api/app/v1/tts_voices/${it.id}/preview`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: it.text }),
      });
      const j = await r.json();
      const audio = j?.data?.audio_base64 || '';
      const success = j?.code === 0 && !!audio;
      await fetch(LOCAL + '/audio', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: it.id, ok: success, audio_base64: audio }),
      });
      success ? ok++ : fail++;
      if ((ok + fail) % 10 === 0) log(`进度 ${ok + fail}/${items.length} (失败 ${fail})`);
    } catch (e) {
      fail++;
      try {
        await fetch(LOCAL + '/audio', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: it.id, ok: false, audio_base64: '' }),
        });
      } catch (_) {}
    }
    await new Promise(r => setTimeout(r, 150));
  }
  await fetch(LOCAL + '/finish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  alert(`完成: 成功 ${ok}, 失败 ${fail}。可关闭此页，回到 Cascade 看后续。`);
  log('全部完成', { ok, fail });
})();
""".strip()


def copy_to_clipboard(text: str) -> bool:
    try:
        proc = subprocess.run(["clip"], input=text.encode("utf-16le"),
                              capture_output=True, shell=True, timeout=3)
        return proc.returncode == 0
    except Exception:
        return False


def main():
    server = HTTPServer((HOST, PORT), IngestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    clipped = copy_to_clipboard(JS_SNIPPET)

    print(f"[serve] 监听 http://{HOST}:{PORT}")
    print()
    print("=" * 60)
    if clipped:
        print("[ok] JS 已自动复制到剪贴板。")
    else:
        print("[warn] 自动复制失败，请手动复制下方 JS。")
    print()
    print("操作:")
    print("  1) 浏览器打开 https://hifly.cc/ (确保已登录)")
    print("  2) F12 -> Console")
    print("  3) Ctrl+V 粘贴回车")
    print("=" * 60)
    if not clipped:
        print()
        print(JS_SNIPPET)
        print()

    # 自动打开 hifly.cc
    try:
        webbrowser.open("https://hifly.cc/")
    except Exception:
        pass

    print()
    print("[serve] 等待浏览器开始推送数据…")
    try:
        # 等列表 + 全部 wav 收完
        while not state["all_done"].is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[serve] 用户中断")

    print()
    print("[serve] 收尾: 写 seed.json + manifest.json …")
    _save_seed_and_manifest()
    print(f"[done] groups={len(state['groups'])}, "
          f"wav_ok={state['received_voices']}, fail={state['failed_voices']}")
    print(f"[done] seed     -> {SEED_PATH}")
    print(f"[done] manifest -> {MANIFEST_PATH}")
    server.shutdown()


if __name__ == "__main__":
    main()
