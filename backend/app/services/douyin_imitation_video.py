"""抖音信息台「做同款（跟创）」：榜单/自传原视频 + 用户图片 → 视频编辑。

2026-10-05 改（按阿里云百炼「Wan3.0视频创作者手册」的视频编辑写法）：
  模型        wan2.7-videoedit  →  wan3.0-video-prime
  media 类型  {"type":"video"}   →  {"type":"reference_video"}（源视频）
              {"type":"reference_image"}（人物图，两代一致）
  parameters  resolution / prompt_extend / watermark  →  再加 ratio(adaptive) 与 duration
  实测：3.0 传 2.7 那代的 type=video 会被上游直接判
        InvalidParameter: Input should be 'first_frame','last_frame','reference_image',
        'reference_video','reference_audio','file' or 'link': input.media.0.type
  实测：不显式传 duration 时上游默认只出 5 秒（15.07 秒原片 → output_video_duration=5.0），
        我们按「输入+输出都计费」收钱，所以必须把 duration 设成真正送审的秒数。
  手册里视频编辑的硬限制（3.0，和我们的 15 秒上限一致）：参考视频不超过 5 个、总时长不大于 15 秒；
  有视频输入时「输入时长 + 输出时长总和不超过 30 秒」；分辨率 480P/720P/1080P；
  比例 16:9/9:16/4:3/3:4/1:1/智能比例；时长 2-30 秒（有视频输入时按上面那条卡）。
  实测（2026-10-05 15:02，480P，同一句「将视频中的人物替换为图片中的人物…」）：
        提交 200 → 86 秒 SUCCEEDED，usage={duration:20.07, input_video_duration:15.07,
        output_video_duration:5.0, SR:480, fps:30, ratio:9:16}。

主链路（用独立 wan key）：
  POST {DOUYIN_IMITATION_HOST | DASHSCOPE_WAN_MAAS_HOST | 默认 MaaS 工作空间端点}
       /api/v1/services/aigc/video-generation/video-synthesis
  {"model":"wan3.0-video-prime",
   "input":{"prompt":"将视频中的人物替换为图片中的人物","media":[{"type":"reference_video","url":..},{"type":"reference_image","url":..}]},
   "parameters":{"resolution":"720P","ratio":"adaptive","duration":15,"prompt_extend":true,"watermark":false}}
  查询 GET {host}/api/v1/tasks/{task_id}
  Key 必须用独立的 wan key（DASHSCOPE_WAN30_API_KEY，116 位）；拿 DASHSCOPE_API_KEY 会被
  "Endpoint.AccessDenied: Workspace endpoint access denied." 拒掉。

兜底链路：wan2.2-animate-mix（DASHSCOPE_API_KEY + image2video/video-synthesis，需单人图）。

实测契约（2026-09-27，生产服务器、真实 key）：
- 取原视频：TikHub GET /api/v1/douyin/web/fetch_one_video_v2?aweme_id=<id>
  → data.aweme_detail.video.play_addr.url_list[0]（抖音 CDN，需带浏览器 UA 下载）
- 换人：DashScope POST /api/v1/services/aigc/image2video/video-synthesis
  {"model":"wan2.2-animate-mix","input":{"image_url":..,"video_url":..,"watermark":false},"parameters":{"mode":"wan-std"}}
  查询 GET /api/v1/tasks/{task_id}
- 两个 URL 必须能被阿里云拉取：我们自己的域名会被拒（实测 "Download https://manage.bhzn.top/... refused"），
  所以图片和视频都先传到 TOS 再用 TOS 公网地址。
- 参考图必须是「单个真人」：否则报 InvalidImage.NoHuman
  （实测原文：The input image has no human body. Please upload other image with single person.）
- 参考视频 ≤30 秒（官方限制），这里默认裁到 DOUYIN_IMITATION_MAX_SECONDS（默认 15 秒）控成本。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
ANIMATE_ENDPOINT = "/api/v1/services/aigc/image2video/video-synthesis"
VIDEOEDIT_ENDPOINT = "/api/v1/services/aigc/video-generation/video-synthesis"
DEFAULT_VIDEOEDIT_HOST = "https://ws-ommi5yczus66lm97.cn-beijing.maas.aliyuncs.com"
DEFAULT_MODEL = "wan3.0-video-prime"   # 3.0 视频编辑（全能参考），2.7 见 LEGACY_MODEL
LEGACY_MODEL = "wan2.7-videoedit"        # 老链路：media 用 type=video


def video_media_type(model: str = "") -> str:
    """源视频在 input.media 里的 type：3.0 用 reference_video，2.7 那代用 video。"""
    name = str(model or "").strip().lower()
    return "reference_video" if name.startswith("wan3") else "video"
DEFAULT_ANIMATE_MODEL = "wan2.2-animate-mix"
DEFAULT_PROMPT = ("编辑视频，视频1中的人物替换为图片1中的人物，保留原视频的动作、镜头、场景与节奏，"
                  "背景和画面其余部分保持不变。")

# 2026-10-05 需求：信息台改名「热门视频跟创」，新增两种模式（下拉选，选不同用不同提示词）。
# 原来的「做同款（换人）」保留为默认模式，老记录/老调用不受影响。
# 提示词按《Wan3.0视频创作者手册》3.1.5「视频编辑」的案例句式写：
#   公式（手册 五、提示词指南「视频编辑公式」）= 编辑对象 + 编辑行为；
#   案例原文形如「编辑视频，视频1中的滑板男人替换为一位短发女人，…滑板动作、运动轨迹和滑板场背景完全保持不变。]
#   「编辑视频，视频1中的女人戴上 图片1 中的帽子，自然贴合头型。…两人的动作、服装和画面其余部分保持不变。」
#   即：开头「编辑视频，」+ 用 视频1 指代参考视频、图片1 指代参考图 + 明确写「哪些保持不变」。
# media 顺序 = [reference_video, reference_image]，所以文案里就是 视频1 / 图片1。
MODE_PROMPTS: Dict[str, str] = {
    # 复刻人物（换人）：视频1里的人 → 图片1里的人，其余全跟原视频
    "person_swap": DEFAULT_PROMPT,
    # 复刻特效：视频1里的特效 → 套到图片1里的人身上
    "effect_copy": "编辑视频，把视频1中的特效应用到图片1中的人物身上，场景、镜头与节奏跟随视频1，画面其余部分保持不变。",
    # 复刻单人动作：图片1里的人 → 做视频1里那个人的动作
    "action_copy": "编辑视频，让图片1中的人物做出视频1中人物的动作，视频1的镜头、节奏与场景保持不变。",
}
MODE_LABELS: Dict[str, str] = {
    "person_swap": "复刻人物（换人）",
    "effect_copy": "复刻特效",
    "action_copy": "复刻单人动作",
}


def normalize_mode(raw: object) -> str:
    value = str(raw or "").strip().lower()
    return value if value in MODE_PROMPTS else "person_swap"


def mode_label(raw: object) -> str:
    return MODE_LABELS.get(normalize_mode(raw), MODE_LABELS["person_swap"])


_DOUYIN_ITEM_ID_RE = re.compile(r"(?:video|note|share/video)/(\d{6,})")
_DOUYIN_SHORT_LINK_RE = re.compile(r"(v\.douyin\.com|iesdouyin\.com/share)", re.I)
_URL_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


async def extract_douyin_item_id(url: str) -> str:
    """从抖音链接里抠作品 id。

    2026-10-05：用户直接贴 https://www.douyin.com/video/7688685833386071653 这种
    作品页链接（不是视频直链），这种链接下载回来是网页不是视频；只要能从链接里拿到
    作品 id，就复用榜单那套 TikHub 解析去取真实播放地址。短链（v.douyin.com/xxx）
    先跟一次跳转再抠 id。
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    match = _DOUYIN_ITEM_ID_RE.search(raw)
    if match:
        return match.group(1)
    if raw.isdigit() and len(raw) >= 12:
        return raw
    if _DOUYIN_SHORT_LINK_RE.search(raw):
        try:
            async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
                resp = await client.get(raw, headers={"User-Agent": _URL_USER_AGENT},
                                        follow_redirects=True)
            match = _DOUYIN_ITEM_ID_RE.search(str(resp.url))
            if match:
                return match.group(1)
        except Exception as exc:  # noqa: BLE001 短链解析失败就按直链处理
            logger.info("[douyin-imitation] 短链解析失败：%s", exc)
    return ""


def _looks_like_web_page(data: bytes) -> bool:
    head = (data or b"")[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def prompt_for_mode(raw_mode: object, prompt: str = "") -> str:
    """用户自己填了提示词就用他的；没填就按选中的模式用对应提示词。"""
    text = str(prompt or "").strip()
    if text:
        return text[:600]
    return MODE_PROMPTS.get(normalize_mode(raw_mode), DEFAULT_PROMPT)
MAX_VIDEO_SECONDS = 30
DEFAULT_MAX_SECONDS = 15
MAX_SOURCE_BYTES = 400 * 1024 * 1024
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# 抖音直链有时为空/过期/403：这几个接口依次兜底，地址也按 candidates 顺序试
SOURCE_ENDPOINTS = (
    "/api/v1/douyin/web/fetch_one_video_v2",
    "/api/v1/douyin/web/fetch_one_video",
    "/api/v1/douyin/app/v3/fetch_one_video_v3",
)

FAIL_HINTS = {
    "Endpoint.AccessDenied": "服务端 wan key 没这个工作空间端点的权限，请联系管理员",
    "InvalidImage.NoHuman": "这张图里没有检测到人物，请换一张只有一个人的清晰照片",
    "InvalidParameter.DataInspection": "素材没通过内容审核，请换一张图片或换一条视频",
}


# 上游状态五花八门（DashScope: SUCCEEDED/RUNNING/FAILED；Comfly: SUCCESS/FAILURE/PENDING），
# 统一成 SUCCESS / RUNNING / FAILED，前端（H5 与 online 两个版本）只认这三个。
_RAW_OK = {"SUCCESS", "SUCCEEDED", "COMPLETED", "FINISHED"}
_RAW_RUNNING = {"RUNNING", "PENDING", "NOT_START", "QUEUED", "PROCESSING", "IN_PROGRESS", "SUBMITTED"}


def normalize_status(raw_status: str) -> str:
    value = str(raw_status or "").strip().upper()
    if value in _RAW_OK:
        return "SUCCESS"
    if value in _RAW_RUNNING or not value:
        return "RUNNING"
    return "FAILED"


def _dashscope_base() -> str:
    return (os.environ.get("DASHSCOPE_BASE_URL") or DASHSCOPE_BASE).strip().rstrip("/")


def _dashscope_key() -> str:
    return (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIYUN_DASHSCOPE_API_KEY") or "").strip()


def _wan_key() -> str:
    """独立的 wan（MaaS 工作空间）key；文档里给的就是它。"""
    return (os.environ.get("DOUYIN_IMITATION_API_KEY")
            or os.environ.get("DASHSCOPE_WAN30_API_KEY") or "").strip()


def _tikhub_base() -> str:
    base = (os.environ.get("TIKHUB_API_BASE") or "https://api.tikhub.io").strip().rstrip("/")
    if base == "https://api.tikhub.dev":
        base = "https://api.tikhub.io"
    return base


def _tikhub_key() -> str:
    return (os.environ.get("TIKHUB_API_KEY") or "").strip()


def _provider() -> str:
    """videoedit（默认，wan2.7）/ animate（兜底，wan2.2 换人）。"""
    value = (os.environ.get("DOUYIN_IMITATION_PROVIDER") or "videoedit").strip().lower()
    return value if value in {"videoedit", "animate"} else "videoedit"


def _videoedit_host() -> str:
    host = (os.environ.get("DOUYIN_IMITATION_HOST") or os.environ.get("DASHSCOPE_WAN_MAAS_HOST")
            or DEFAULT_VIDEOEDIT_HOST).strip().rstrip("/")
    return host


def _model() -> str:
    default = DEFAULT_MODEL if _provider() == "videoedit" else DEFAULT_ANIMATE_MODEL
    return (os.environ.get("DOUYIN_IMITATION_MODEL") or default).strip()


def default_prompt() -> str:
    return (os.environ.get("DOUYIN_IMITATION_PROMPT") or DEFAULT_PROMPT).strip()


def _max_seconds() -> int:
    try:
        value = int(float(os.environ.get("DOUYIN_IMITATION_MAX_SECONDS") or DEFAULT_MAX_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_SECONDS
    return max(3, min(MAX_VIDEO_SECONDS, value))


def friendly_error(code: str, message: str) -> str:
    """把上游错误翻成用户能懂的话。"""
    raw = str(message or "").strip()
    for key, hint in FAIL_HINTS.items():
        if key and (key in raw or key == str(code or "").strip()):
            return hint
    if "ConnectionRefused" in raw or "Download" in raw and "refused" in raw:
        return "素材地址阿里云拉不到，请重试（会自动改走 TOS 地址）"
    return raw[:200] or "生成失败"


def pick_play_urls(detail: Dict[str, Any]) -> list:
    """从抖音作品详情里挑出所有可下载的播放地址（按优先级去重）。"""
    video = (detail or {}).get("video") or {}
    out: list = []

    def add(node: Any) -> None:
        for url in ((node or {}).get("url_list") or []):
            if isinstance(url, str) and url.startswith("http") and url not in out:
                out.append(url)

    for key in ("play_addr_h264", "play_addr_265", "play_addr"):
        add(video.get(key))
    for item in (video.get("bit_rate") or []):
        add(item.get("play_addr"))
    for item in (video.get("misc_download_addrs") or []):
        if isinstance(item, dict):
            add(item)
    add(video.get("download_addr"))
    return out


def pick_play_url(detail: Dict[str, Any]) -> str:
    """兼容老调用：返回第一个候选地址。"""
    urls = pick_play_urls(detail)
    return urls[0] if urls else ""


async def resolve_source_video(item_id: str) -> Dict[str, Any]:
    """按作品 id 取原视频直链（TikHub 计费 1 次）。"""
    clean_id = str(item_id or "").strip()
    if not clean_id.isdigit():
        return {"ok": False, "error": "缺少作品 id，无法取原视频"}
    key = _tikhub_key()
    if not key:
        return {"ok": False, "error": "服务端未配置 TIKHUB_API_KEY"}
    detail: Dict[str, Any] = {}
    last_error = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), trust_env=False) as client:
        for endpoint in SOURCE_ENDPOINTS:
            try:
                resp = await client.get(_tikhub_base() + endpoint, params={"aweme_id": clean_id},
                                        headers={"Authorization": f"Bearer {key}"})
            except Exception as exc:  # noqa: BLE001
                last_error = f"取原视频失败：{exc}"
                continue
            if resp.status_code != 200:
                last_error = f"取原视频失败：HTTP {resp.status_code}"
                continue
            detail = ((resp.json() or {}).get("data") or {}).get("aweme_detail") or {}
            if pick_play_urls(detail):
                break
    urls = pick_play_urls(detail)
    if not urls:
        return {"ok": False, "error": last_error or "这条作品拿不到可下载的视频地址"}
    url = urls[0]
    duration_ms = ((detail.get("video") or {}).get("duration") or 0)
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 0
    return {"ok": True, "url": url, "urls": urls, "duration_ms": duration_ms,
            "desc": str(detail.get("desc") or "")[:80]}


async def _download(url: str, limit: int = MAX_SOURCE_BYTES) -> Tuple[Optional[bytes], str]:
    """下载素材；抖音直链会被 403，所以带上 Referer 再试一次。"""
    headers_variants = (
        {"User-Agent": BROWSER_UA},
        {"User-Agent": BROWSER_UA, "Referer": "https://www.douyin.com/"},
    )
    last = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0), trust_env=False,
                                 follow_redirects=True) as client:
        for headers in headers_variants:
            try:
                resp = await client.get(url, headers=headers)
            except Exception as exc:  # noqa: BLE001
                last = f"下载素材失败：{exc}"
                continue
            if resp.status_code != 200:
                last = f"下载素材失败：HTTP {resp.status_code}"
                continue
            data = resp.content
            if len(data) > limit:
                return None, "素材文件过大"
            return data, ""
    return None, last or "下载素材失败"


async def _download_first(urls: list, limit: int = MAX_SOURCE_BYTES) -> Tuple[Optional[bytes], str, str]:
    """按候选地址依次下载，返回 (数据, 用了哪个地址, 错误)。"""
    last = ""
    for url in (urls or [])[:5]:
        data, err = await _download(url, limit=limit)
        if data:
            return data, url, ""
        last = err
    return None, "", last


async def store_generated_video(video_url: str, *, title: str = "") -> Dict[str, Any]:
    """把上游成片转存到我们自己的 TOS（阿里云出的 OSS 链接带 Expires，过期就打不开了）。

    返回 {ok, public_url, asset_id, file_size, object_key}；失败返回 {ok: False, error}。
    """
    source = str(video_url or "").strip()
    if not source.startswith(("http://", "https://")):
        return {"ok": False, "error": "成片地址无效"}
    data, err = await _download(source, limit=MAX_SOURCE_BYTES)
    if err:
        return {"ok": False, "error": f"下载成片失败：{err}"}
    from ..api.assets import _run_asset_upload_io, _save_upload_file_or_tos

    import io as _io

    handle = _io.BytesIO(data)
    handle.name = "douyin-imitation.mp4"     # assets 上传助手会取 name/suffix
    asset_id, object_key, file_size, public_url = await _run_asset_upload_io(
        _save_upload_file_or_tos, handle, ".mp4", "video/mp4"
    )
    if not public_url:
        return {"ok": False, "error": "成片转存失败：存储没返回公网地址"}
    logger.info("[douyin-imitation] stored title=%s asset=%s size=%s", str(title)[:40], asset_id, file_size)
    return {"ok": True, "public_url": public_url, "asset_id": asset_id,
            "file_size": int(file_size or len(data)), "object_key": object_key}


async def fetch_video_bytes(video_url: str, limit: int = MAX_SOURCE_BYTES) -> Tuple[Optional[bytes], str]:
    """按地址取成片字节（下载接口用）。"""
    return await _download(video_url, limit=limit)


async def _upload_tos(data: bytes, suffix: str, content_type: str) -> Tuple[str, str]:
    """上传到 TOS，返回 (公网地址, 错误)。阿里云拉不到我们自己的域名，必须走 TOS。"""
    from ..api.assets import _run_asset_upload_io, _save_upload_file_or_tos

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(data)
        temp_path = pathlib.Path(fh.name)
    try:
        with temp_path.open("rb") as handle:
            _asset_id, _key, _size, public_url = await _run_asset_upload_io(
                _save_upload_file_or_tos, handle, suffix, content_type
            )
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass
    if not public_url:
        return "", "素材上传失败：TOS 没返回公网地址"
    return public_url, ""


def probe_video_seconds(data: bytes) -> float:
    """ffprobe 读视频实际时长（秒）。读不到返回 0（调用方按上限兜底收费，避免少收）。"""
    from pathlib import Path

    if not data:
        return 0.0
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "probe.mp4"
        path.write_bytes(data)
        try:
            done = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, timeout=60, text=True,
            )
            return max(0.0, float(str(done.stdout or "").strip() or 0))
        except Exception as exc:  # noqa: BLE001 探测失败就交给上限
            logger.info("[douyin-imitation] 读取视频时长失败：%s", exc)
            return 0.0


def trim_video(data: bytes, max_seconds: int) -> Tuple[bytes, str]:
    """裁到 max_seconds（优先 -c copy，失败则重编码）。"""
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        src = Path(folder) / "src"
        dst = Path(folder) / "dst.mp4"
        src.write_bytes(data)
        for args in (
            ["ffmpeg", "-y", "-i", str(src), "-t", str(max_seconds), "-c", "copy", str(dst)],
            ["ffmpeg", "-y", "-i", str(src), "-t", str(max_seconds), "-movflags", "+faststart", str(dst)],
        ):
            try:
                done = subprocess.run(args, capture_output=True, timeout=180)
            except Exception:  # noqa: BLE001
                continue
            if done.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                return dst.read_bytes(), ""
        return data, ""


RESOLUTIONS = ("720P", "1080P")


def normalize_resolution(raw: object) -> str:
    value = str(raw or "").strip().upper()
    return value if value in RESOLUTIONS else "720P"


async def submit_imitation(image_url: str, source_video_url: str, *, mode: str = "wan-std",
                           prompt: str = "", resolution: str = "720P", model: str = "",
                           duration_seconds: int = 0) -> Dict[str, Any]:
    """提交做同款任务：返回 {ok, task_id, ...} 或 {ok: False, error}。"""
    image = str(image_url or "").strip()
    video = str(source_video_url or "").strip()
    if not image.startswith(("http://", "https://")) or not video.startswith(("http://", "https://")):
        return {"ok": False, "error": "素材地址无效"}
    provider = _provider()
    if provider == "videoedit":
        key = _wan_key()
        if not key:
            return {"ok": False, "error": "服务端未配置独立 wan key（DASHSCOPE_WAN30_API_KEY）"}
        edit_model = str(model or _model()).strip() or DEFAULT_MODEL
        source_media_type = video_media_type(edit_model)
        parameters: Dict[str, Any] = {"resolution": normalize_resolution(resolution),
                                      "prompt_extend": True, "watermark": False}
        if source_media_type == "reference_video":
            # 3.0：比例跟随原片；duration 不传上游只出 5 秒，必须显式给成片秒数
            parameters["ratio"] = "adaptive"
            if int(duration_seconds or 0) > 0:
                parameters["duration"] = int(duration_seconds)
        body = {
            "model": edit_model,
            "input": {"prompt": str(prompt or "").strip()[:600] or default_prompt(),
                      "media": [{"type": source_media_type, "url": video},
                                {"type": "reference_image", "url": image}]},
            "parameters": parameters,
        }
        url = _videoedit_host() + VIDEOEDIT_ENDPOINT
    else:
        key = _dashscope_key()
        if not key:
            return {"ok": False, "error": "服务端未配置 DASHSCOPE_API_KEY"}
        body = {
            "model": _model(),
            "input": {"image_url": image, "video_url": video, "watermark": False},
            "parameters": {"mode": mode if mode in {"wan-std", "wan-pro"} else "wan-std"},
        }
        url = _dashscope_base() + ANIMATE_ENDPOINT
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), trust_env=False) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable",
                         "Content-Type": "application/json"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"提交做同款任务失败：{exc}"}
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:  # noqa: BLE001
            detail = {}
        return {"ok": False, "error": friendly_error(str(detail.get("code") or ""),
                                                     str(detail.get("message") or resp.text)[:200])}
    try:
        resp_payload = resp.json() or {}
    except Exception:  # noqa: BLE001
        resp_payload = {"_raw": str(resp.text or "")[:4000]}
    task_id = str(((resp_payload.get("output") or {}) or {}).get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "做同款任务没有返回任务号"}
    used_model = str(body.get("model") or _model())
    logger.info("[douyin-imitation] submit provider=%s model=%s task=%s", provider, used_model, task_id)
    result = {"ok": True, "task_id": task_id, "model": used_model, "provider": provider,
              # 管理后台留痕：我们提交给上游的原文 + 上游这次返回的原文
              "request_body": body,
              "response_body": json.dumps(resp_payload, ensure_ascii=False)[:20000]}
    if provider == "videoedit":
        result["prompt"] = body["input"]["prompt"]
        result["resolution"] = normalize_resolution(resolution)
        if body["parameters"].get("duration"):
            result["duration"] = int(body["parameters"]["duration"])
    else:
        result["mode"] = body["parameters"]["mode"]
    return result


async def query_imitation(task_id: str) -> Dict[str, Any]:
    """查询换人任务：{ok, status, progress, video_url, fail_reason, done}。"""
    clean_id = str(task_id or "").strip()
    if not clean_id:
        return {"ok": False, "error": "缺少任务号"}
    provider = _provider()
    key = _wan_key() if provider == "videoedit" else _dashscope_key()
    if not key:
        return {"ok": False, "error": "服务端未配置做同款所需的模型 Key"}
    host = _videoedit_host() if provider == "videoedit" else _dashscope_base()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), trust_env=False) as client:
            resp = await client.get(host + "/api/v1/tasks/" + clean_id,
                                    headers={"Authorization": f"Bearer {key}"})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"查询换人任务失败：{exc}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"查询换人任务失败：HTTP {resp.status_code}"}
    try:
        query_payload = resp.json() or {}
        output = (query_payload.get("output") or {})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"解析换人结果失败：{exc}"}
    raw_status = str(output.get("task_status") or "").upper()
    status = normalize_status(raw_status)
    video_url = str(output.get("video_url") or "").strip()
    return {
        "ok": True,
        "task_id": clean_id,
        "status": status,
        "raw_status": raw_status,
        "progress": "100%" if status == "SUCCESS" else "",
        "video_url": video_url if status == "SUCCESS" else "",
        "fail_reason": (friendly_error(str(output.get("code") or ""), str(output.get("message") or ""))
                        if status == "FAILED" else ""),
        "done": status in {"SUCCESS", "FAILED"},
        "response_body": json.dumps(query_payload, ensure_ascii=False)[:20000],
    }


async def prepare_imitation(image_url: str, item_id: str = "", prompt: str = "", *,
                            video_url: str = "", mode: str = "person_swap",
                            resolution: str = "720P") -> Dict[str, Any]:
    """把素材准备好并提交：用户图 + 原视频都转到 TOS，再按模式提交。

    2026-10-05：视频来源支持两种 —— 榜单作品（item_id）或用户自己给的视频地址
    （video_url，可以是粘贴的直链，也可以是本机上传后拿到的公网地址）。
    """
    image_raw = str(image_url or "").strip()
    if not image_raw.startswith(("http://", "https://")):
        return {"ok": False, "error": "参考图地址无效，请重新上传"}
    custom_video = str(video_url or "").strip()
    if custom_video:
        if not custom_video.startswith(("http://", "https://")):
            return {"ok": False, "error": "视频地址无效：请填 http(s) 链接，或改为上传本地视频"}
        url_item_id = await extract_douyin_item_id(custom_video)
        if url_item_id:
            # 抖音作品页/短链：先解析出作品 id，再走榜单同一套 TikHub 取真实播放地址
            source = await resolve_source_video(url_item_id)
            if not source.get("ok"):
                return source
            video_bytes, _used_url, err = await _download_first(source.get("urls") or [source["url"]])
            if err:
                return {"ok": False, "error": err}
        else:
            video_bytes, err = await _download(custom_video, limit=MAX_SOURCE_BYTES)
            if err:
                return {"ok": False, "error": f"读取视频失败：{err}"}
            if _looks_like_web_page(video_bytes):
                return {"ok": False, "error": "这个地址返回的是网页不是视频：抖音作品链接请用 www.douyin.com/video/… 这种形式，"
                                              "或者直接上传本地视频"}
            source = {"ok": True, "desc": "自定义视频", "url": custom_video}
    else:
        if not str(item_id or "").strip():
            return {"ok": False, "error": "请先填视频链接、上传本地视频，或从榜单里点「跟创」"}
        source = await resolve_source_video(item_id)
        if not source.get("ok"):
            return source
        video_bytes, _used_url, err = await _download_first(source.get("urls") or [source["url"]])
        if err:
            return {"ok": False, "error": err}
    max_seconds = _max_seconds()
    # 2026-10-05：按原视频实际时长计费（超过上限才按上限），这里先探一次真实时长
    source_seconds = await asyncio.to_thread(probe_video_seconds, video_bytes)
    trimmed, _warn = trim_video(video_bytes, max_seconds)
    # 关键：计费必须以「真正送去生成的视频」为准 —— trim_video 在 ffmpeg 两次都失败时
    # 会把原片原样返回，如果那时按 15 秒收费却把 60 秒原片送上去，就是我们自己亏钱。
    sent_seconds = await asyncio.to_thread(probe_video_seconds, trimmed)
    if sent_seconds <= 0:
        if source_seconds > float(max_seconds) + 0.5:
            return {"ok": False,
                    "error": "这个视频裁剪后确认不了时长（可能是异常编码），已取消提交，请换一个视频或上传本地文件"}
        sent_seconds = float(source_seconds or max_seconds)
    if sent_seconds > float(max_seconds) + 0.5:
        return {"ok": False,
                "error": f"视频裁剪后仍有 {sent_seconds:.1f} 秒（本档上限 {max_seconds} 秒），已取消提交，"
                         "请换更短的视频"}
    effective_seconds = float(max(1, int(sent_seconds) if float(sent_seconds).is_integer()
                                  else int(sent_seconds) + 1))
    video_tos, err = await _upload_tos(trimmed, ".mp4", "video/mp4")
    if err:
        return {"ok": False, "error": err}
    image_bytes, err = await _download(image_raw, limit=20 * 1024 * 1024)
    if err:
        return {"ok": False, "error": f"读取参考图失败：{err}"}
    suffix = ".png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    image_tos, err = await _upload_tos(image_bytes, suffix, "image/png" if suffix == ".png" else "image/jpeg")
    if err:
        return {"ok": False, "error": err}
    final_prompt = prompt_for_mode(mode, prompt)
    result = await submit_imitation(image_tos, video_tos, prompt=final_prompt, resolution=resolution,
                                    duration_seconds=int(effective_seconds))
    if not result.get("ok"):
        return result
    result.update({"source_desc": source.get("desc") or "",
                   "source_seconds": round(float(source_seconds or 0.0), 2),
                   "sent_seconds": round(float(sent_seconds or 0.0), 2),
                   "video_seconds": int(effective_seconds),
                   "image_url": image_tos, "video_url": video_tos,
                   "mode": normalize_mode(mode), "mode_label": mode_label(mode),
                   "resolution": normalize_resolution(result.get("resolution") or resolution),
                   "prompt": final_prompt})
    return result
