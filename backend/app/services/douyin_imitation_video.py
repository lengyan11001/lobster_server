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

主链路（默认标准 DashScope 端点 + DASHSCOPE_API_KEY）：
  POST {DOUYIN_IMITATION_HOST | DASHSCOPE_WAN_MAAS_HOST | https://dashscope.aliyuncs.com}
       /api/v1/services/aigc/video-generation/video-synthesis
  {"model":"wan3.0-video",
   "input":{"prompt":"将视频中的人物替换为图片中的人物","media":[{"type":"reference_video","url":..},{"type":"reference_image","url":..}]},
   "parameters":{"resolution":"720P","ratio":"adaptive","duration":15,"prompt_extend":true,"watermark":false}}
  查询 GET {host}/api/v1/tasks/{task_id}
  2026-11-25 实测（生产账号）：标准端点 + DASHSCOPE_API_KEY 提交 200，480P/5 秒 108 秒 SUCCEEDED；
  同一账号的 DASHSCOPE_WAN30_API_KEY（MaaS 工作空间 key）无论配 MaaS 端点还是标准端点都返回
  403 AccessDenied.Unpurchased（该工作空间没有这个模型），所以默认不再使用工作空间 key。
  只有显式配置 DOUYIN_IMITATION_HOST/DASHSCOPE_WAN_MAAS_HOST 指向 MaaS 端点时才用 wan key。

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
# 2026-11-25 实测：wan3.0-video + 标准 DashScope 端点 + DASHSCOPE_API_KEY 正常出片
# （480P/5 秒，108 秒 SUCCEEDED）；同一账号的 MaaS 工作空间 key 调 3.0 反而 403
# AccessDenied.Unpurchased，所以默认走标准端点；只有显式配置 DOUYIN_IMITATION_HOST /
# DASHSCOPE_WAN_MAAS_HOST 才走 MaaS 工作空间（那时用独立 wan key）。
DEFAULT_VIDEOEDIT_HOST = DASHSCOPE_BASE
DEFAULT_MODEL = "wan3.0-video"   # 3.0 视频编辑（全能参考），2.7 见 LEGACY_MODEL
LEGACY_MODEL = "wan2.7-videoedit"        # 老链路：media 用 type=video


def video_media_type(model: str = "") -> str:
    """源视频在 input.media 里的 type：3.0 用 reference_video，2.7 那代用 video。"""
    name = str(model or "").strip().lower()
    return "reference_video" if name.startswith("wan3") else "video"
DEFAULT_ANIMATE_MODEL = "wan2.2-animate-mix"

# 2026-10-05 需求：信息台改名「热门视频跟创」，新增两种模式（下拉选，选不同用不同提示词）。
# 原来的「做同款（换人）」保留为默认模式，老记录/老调用不受影响。
# 2026-11-25 按官方《万相3.0 视频生成 Prompt 指南》重写（视频编辑 / 视频参考图编辑）：
#   1) 开头固定「编辑视频，」；参考素材按上传顺序编号：视频1 = 源视频，图片1 = 用户图；
#   2) 公式 = 总体描述 + 参考素材引用 + 编辑行为 + 「保持不变」清单 + 负向清单；
#   3) 官方案例强调「写清哪些保持不变」（动作、轨迹、背景、镜头…），不写就会漂移；
#   4) 参考图编辑要复述人物特征（五官/发型/服装/体型），并写清自然贴合、无穿模。
# media 顺序 = [reference_video, reference_image]，所以文案里就是 视频1 / 图片1。
MODE_PROMPTS: Dict[str, str] = {
    # 复刻人物（换人）：视频1里的人 → 图片1里的人，其余全跟原视频
    "person_swap": (
        "编辑视频，视频1中的人物替换为图片1中的人物：使用图片1的五官、脸型、发型、肤色与服装，"
        "自然贴合视频1人物的身体比例、姿态和光影。完整保留视频1的动作、表情、口型、手势、走位与运动轨迹；"
        "镜头机位、景别、运动、背景、光线、色调、画幅和节奏全部保持不变。"
        "人物边缘过渡自然，无穿模、无重影、无面部畸变。"
        "负向：无多余人物、无字幕水印、无画面模糊。"
    ),
    # 复刻特效：视频1里的特效 → 套到图片1里的人身上
    "effect_copy": (
        "编辑视频，图片1中的人物按照视频1的特效与动作生成：复刻视频1中特效的形态、颜色、亮度、"
        "粒子与能量流动方向，以及出现、爆发、消散的时机；人物同步完成视频1中的动作，"
        "动作幅度、速度和节奏与视频1一致。人物保留图片1的五官、发型与服装，"
        "场景、镜头机位、构图、背景、光线与时长沿用视频1。"
        "特效与人物自然融合，遮挡、投影和光照正确，无穿模。"
        "负向：无多余人物、无字幕水印、无特效糊成一片、无面部畸变。"
    ),
    # 复刻单人动作：图片1里的人 → 做视频1里那个人的动作
    "action_copy": (
        "编辑视频，让图片1中的人物完成视频1中人物的动作：复刻视频1的肢体动作、手势、走位、转身、"
        "表情变化与节奏，动作幅度、速度和时机逐帧对齐视频1。人物保留图片1的五官、发型与服装，"
        "身体比例与视频1人物自然匹配。镜头机位、景深、运动、景别、背景、光线与色调全部沿用视频1。"
        "动作连贯自然，关节无扭曲，手脚不穿模。"
        "负向：无多余人物、无字幕水印、无动作跳变、无面部畸变。"
    ),
}
DEFAULT_PROMPT = MODE_PROMPTS["person_swap"]
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
    "AccessDenied.Unpurchased": "上游模型没开通（AccessDenied.Unpurchased），请管理员开通 wan3.0-video",
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
            or _dashscope_base()).strip().rstrip("/")
    return host


def _videoedit_key(host: str = "") -> str:
    """按端点选 key：MaaS 工作空间端点用独立 wan key，标准 DashScope 端点用 DASHSCOPE_API_KEY。"""
    explicit = (os.environ.get("DOUYIN_IMITATION_API_KEY") or "").strip()
    if explicit:
        return explicit
    target = (host or _videoedit_host()).lower()
    if "maas.aliyuncs.com" in target:
        return _wan_key() or _dashscope_key()
    return _dashscope_key() or _wan_key()


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


async def store_generated_video_bytes(data: bytes, *, title: str = "") -> Dict[str, Any]:
    """把成片字节转存到 TOS，返回和 store_generated_video 相同的结构。"""
    if not data:
        return {"ok": False, "error": "成片为空"}
    from ..api.assets import _run_asset_upload_io, _save_upload_file_or_tos

    import io as _io

    handle = _io.BytesIO(data)
    handle.name = "douyin-imitation.mp4"
    asset_id, object_key, file_size, public_url = await _run_asset_upload_io(
        _save_upload_file_or_tos, handle, ".mp4", "video/mp4"
    )
    if not public_url:
        return {"ok": False, "error": "成片转存失败：存储没返回公网地址"}
    size = int(file_size or len(data))
    logger.info("[douyin-imitation] stored bytes title=%s asset=%s size=%s", str(title)[:40], asset_id, size)
    return {"ok": True, "public_url": public_url, "asset_id": asset_id,
            "file_size": int(file_size or len(data)), "object_key": object_key}


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
    return await store_generated_video_bytes(data, title=title)


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


def _atempo_chain(ratio: float) -> list[str]:
    """把播放倍率拆成 ffmpeg atempo 支持的 0.5-2.0 段。"""
    value = max(0.01, float(ratio or 1.0))
    out: list[str] = []
    while value > 2.0:
        out.append("atempo=2.0")
        value /= 2.0
    while value < 0.5:
        out.append("atempo=0.5")
        value /= 0.5
    out.append("atempo=%.6f" % value)
    return out


def build_voice_mix_filter(video_seconds: float, audio_seconds: float) -> str:
    """生成「把配音铺满视频时长」的 ffmpeg filter。

    - 配音比视频长：加速；配音比视频短：放慢；
    - 最后 apad + atrim，保证和视频时长严格一致。
    """
    target = max(0.1, float(video_seconds or 0.0))
    audio = max(0.0, float(audio_seconds or 0.0))
    if audio <= 0:
        return "[1:a]apad,atrim=0:%.3f[a]" % target
    ratio = audio / target
    filters = _atempo_chain(ratio)
    filters.append("apad")
    filters.append("atrim=0:%.3f" % target)
    return "[1:a]" + ",".join(filters) + "[a]"


async def apply_voice_to_video(video_url: str, audio_url: str, *, title: str = "") -> Dict[str, Any]:
    """下载成片和配音，用 ffmpeg 替换音轨，再转存 TOS。"""
    video_src = str(video_url or "").strip()
    audio_src = str(audio_url or "").strip()
    if not video_src.startswith(("http://", "https://")) or not audio_src.startswith(("http://", "https://")):
        return {"ok": False, "error": "成片或配音地址无效"}
    video_bytes, err = await _download(video_src, limit=MAX_SOURCE_BYTES)
    if err or not video_bytes:
        return {"ok": False, "error": "下载成片失败：%s" % (err or "空文件")}
    audio_bytes, err = await _download(audio_src, limit=60 * 1024 * 1024)
    if err or not audio_bytes:
        return {"ok": False, "error": "下载配音失败：%s" % (err or "空文件")}
    video_seconds = await asyncio.to_thread(probe_video_seconds, video_bytes)
    audio_seconds = await asyncio.to_thread(probe_video_seconds, audio_bytes)
    if video_seconds <= 0:
        return {"ok": False, "error": "读不到成片时长，无法对齐配音"}
    filter_complex = build_voice_mix_filter(video_seconds, audio_seconds)
    with tempfile.TemporaryDirectory() as folder:
        vpath = pathlib.Path(folder) / "video.mp4"
        apath = pathlib.Path(folder) / "voice.mp3"
        outpath = pathlib.Path(folder) / "out.mp4"
        vpath.write_bytes(video_bytes)
        apath.write_bytes(audio_bytes)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(vpath), "-i", str(apath),
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac",
            "-t", "%.3f" % video_seconds,
            "-movflags", "+faststart", str(outpath),
        ]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=300)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "配音替换失败：%s" % exc}
        if done.returncode != 0 or not outpath.exists() or outpath.stat().st_size <= 0:
            detail = (done.stderr or b"")[:300].decode("utf-8", "ignore").strip()
            return {"ok": False, "error": "配音替换失败：%s" % (detail or "ffmpeg 未生成输出")}
        processed = outpath.read_bytes()
    stored = await store_generated_video_bytes(processed, title=title)
    stored.update({"video_seconds": round(video_seconds, 2), "audio_seconds": round(audio_seconds, 2)})
    return stored


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


RESOLUTIONS = ("480P", "720P", "1080P")   # 2026-10-05：上游切 3.0（wan3.0-video-prime）后支持 480P


def normalize_resolution(raw: object) -> str:
    value = str(raw or "").strip().upper()
    return value if value in RESOLUTIONS else "720P"


async def submit_imitation(image_url: str, source_video_url: str, *, mode: str = "wan-std",
                           prompt: str = "", resolution: str = "720P", model: str = "",
                           duration_seconds: int = 0, reference_audio_url: str = "") -> Dict[str, Any]:
    """提交做同款任务：返回 {ok, task_id, ...} 或 {ok: False, error}。"""
    image = str(image_url or "").strip()
    video = str(source_video_url or "").strip()
    if not image.startswith(("http://", "https://")) or not video.startswith(("http://", "https://")):
        return {"ok": False, "error": "素材地址无效"}
    provider = _provider()
    if provider == "videoedit":
        host = _videoedit_host()
        key = _videoedit_key(host)
        if not key:
            return {"ok": False, "error": "服务端未配置 DASHSCOPE_API_KEY（或 DOUYIN_IMITATION_API_KEY）"}
        edit_model = str(model or _model()).strip() or DEFAULT_MODEL
        source_media_type = video_media_type(edit_model)
        parameters: Dict[str, Any] = {"resolution": normalize_resolution(resolution),
                                      "prompt_extend": True, "watermark": False}
        if source_media_type == "reference_video":
            # 3.0：比例跟随原片；duration 不传上游只出 5 秒，必须显式给成片秒数
            parameters["ratio"] = "adaptive"
            if int(duration_seconds or 0) > 0:
                parameters["duration"] = int(duration_seconds)
        media = [{"type": source_media_type, "url": video},
                 {"type": "reference_image", "url": image}]
        if str(reference_audio_url or "").strip():
            # 3.0 支持 reference_audio：让模型按选定声音的口型/节奏生成；
            # 不支持时下面会自动去掉这个媒体再提交一次，成片仍由后处理换轨兜底。
            media.append({"type": "reference_audio", "url": str(reference_audio_url).strip()})
        body = {
            "model": edit_model,
            "input": {"prompt": str(prompt or "").strip()[:600] or default_prompt(),
                      "media": media},
            "parameters": parameters,
        }
        url = host + VIDEOEDIT_ENDPOINT
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
            if resp.status_code != 200 and provider == "videoedit" and str(reference_audio_url or "").strip():
                # 老账号/老模型可能不认 reference_audio：去掉它重提一次，别让整个任务失败
                retry_body = {
                    **body,
                    "input": {
                        **body["input"],
                        "media": [m for m in body["input"]["media"] if m.get("type") != "reference_audio"],
                    },
                }
                retry = await client.post(
                    url,
                    json=retry_body,
                    headers={"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable",
                             "Content-Type": "application/json"},
                )
                if retry.status_code == 200:
                    body = retry_body
                    resp = retry
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
    if provider == "videoedit":
        host = _videoedit_host()
        key = _videoedit_key(host)
    else:
        host = _dashscope_base()
        key = _dashscope_key()
    if not key:
        return {"ok": False, "error": "服务端未配置做同款所需的模型 Key"}
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


# 2026-10-05：成片时长放给用户选。手册（Wan3.0）限制：有视频输入时「输入时长 + 输出时长 ≤ 30 秒」，
# 最短 2 秒；duration_seconds=0 表示跟输入视频一样长（默认）。
MIN_OUTPUT_SECONDS = 2
MAX_TOTAL_SECONDS = 30


def resolve_output_seconds(raw: object, input_seconds: float) -> int:
    """用户选的成片秒数（0 = 跟输入视频一样长）；按「输入 + 输出 ≤ 30 秒」夹住。

    2026-10-05 事故：上游是按真实秒数校验的 ——
      input_video_duration(15.07s) + duration(16.0s) = 31.07s exceeds 30s limit
    以前这里把输入时长四舍五入成整数（15.07 → 15）再算上限，15 + 15 = 30 看着合规，
    实际 30.07 秒被上游拒掉。现在用精确的小数时长算上限，并且留 0.05 秒浮点余量；
    不选时长（0）时也照样夹，不能超过上限。
    """
    import math
    try:
        want = int(float(raw or 0))
    except (TypeError, ValueError):
        want = 0
    try:
        sent = float(input_seconds or 0)
    except (TypeError, ValueError):
        sent = 0.0
    if sent <= 0:
        sent = float(MAX_TOTAL_SECONDS)
    ceiling = int(math.floor(MAX_TOTAL_SECONDS - sent - 0.05))
    if ceiling < MIN_OUTPUT_SECONDS:
        ceiling = MIN_OUTPUT_SECONDS
    if want <= 0:
        return max(MIN_OUTPUT_SECONDS, min(int(math.floor(sent)), ceiling))
    return max(MIN_OUTPUT_SECONDS, min(want, ceiling))


async def prepare_voice_audio_for_video(
    video_url: str,
    *,
    voice_id: str,
    voice_source: str,
    db: Any,
    user_id: int,
) -> Dict[str, Any]:
    """用跟创真正送去生成的裁剪后视频转写，再用选定声音生成配音。"""
    clean_voice = str(voice_id or "").strip()
    if not clean_voice:
        return {"ok": False, "error": "没有选择声音"}
    if db is None or not int(user_id or 0):
        return {"ok": False, "error": "声音跟创缺少账号上下文"}
    try:
        from ..api.cutcli_templates import _load_sutui_token_for_stt  # noqa: PLC0415
        from ..api.wechat_channels_transcript import _process_one_video  # noqa: PLC0415

        token, token_source = _load_sutui_token_for_stt(db, int(user_id))
        if not token:
            return {"ok": False, "error": "文案转写服务未配置，暂时不能选声音跟创"}
        # 转写/合成可能几十秒，先取完数据并释放数据库事务，避免 Postgres
        # idle_in_transaction_session_timeout 在慢调用期间把连接杀掉。
        voice_row = None
        if str(voice_source or "").strip().lower() != "system":
            from ..models import UserHiflyVoiceAsset  # noqa: PLC0415

            voice_row = (
                db.query(UserHiflyVoiceAsset)
                .filter(
                    UserHiflyVoiceAsset.user_id == int(user_id),
                    UserHiflyVoiceAsset.hifly_voice_id == clean_voice,
                    UserHiflyVoiceAsset.status == "success",
                )
                .first()
            )
            if voice_row is None:
                return {"ok": False, "error": "这个声音不存在或不属于当前账号，请重新选择"}
            db.expunge(voice_row)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        with tempfile.TemporaryDirectory(prefix="imitation_voice_") as folder:
            item = await asyncio.to_thread(
                _process_one_video,
                job_id="imitation_voice_" + os.urandom(4).hex(),
                item={"video_url": video_url},
                index=0,
                token=token,
                token_source=token_source,
                job_dir=pathlib.Path(folder),
            )
        text = str((item or {}).get("transcript") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[douyin-imitation] 声音跟创转写失败: %s", exc)
        return {"ok": False, "error": "原视频文案提取失败：%s" % str(exc)[:200]}
    if not text:
        return {"ok": False, "error": "原视频没有可转写的口播文案，改选原声再试"}

    if str(voice_source or "").strip().lower() == "system":
        try:
            from . import device_tts  # noqa: PLC0415

            audio_url = await asyncio.to_thread(
                device_tts.synthesize, text, token=token, voice_id=clean_voice
            )
            audio_bytes, err = await _download(audio_url, limit=60 * 1024 * 1024)
            if err or not audio_bytes:
                return {"ok": False, "error": "系统声音配音下载失败：%s" % (err or "空文件")}
            audio_tos, err = await _upload_tos(audio_bytes, ".mp3", "audio/mpeg")
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "audio_url": audio_tos, "text": text}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[douyin-imitation] 系统声音配音失败: %s", exc)
            return {"ok": False, "error": "系统声音配音失败：%s" % str(exc)[:200]}

    try:
        from ..api.hifly_assets import _preview_tts_audio, _voice_provider  # noqa: PLC0415

        result = await _preview_tts_audio(
            provider=_voice_provider(voice_row),
            row=voice_row,
            text=text,
            rate=1.0,
            volume=1.0,
            pitch=0,
            emotion="happy",
        )
        audio_tos, err = await _upload_tos(result["audio_bytes"], ".mp3", "audio/mpeg")
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "audio_url": audio_tos, "text": text}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[douyin-imitation] 我的声音配音失败: %s", exc)
        return {"ok": False, "error": "我的声音配音失败：%s" % str(exc)[:200]}


async def prepare_imitation(image_url: str, item_id: str = "", prompt: str = "", *,
                            video_url: str = "", mode: str = "person_swap",
                            resolution: str = "720P", duration_seconds: int = 0,
                            voice_id: str = "", voice_source: str = "",
                            db: Any = None, user_id: int = 0) -> Dict[str, Any]:
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
    voice_audio_url = ""
    voice_text = ""
    if str(voice_id or "").strip():
        voice_prep = await prepare_voice_audio_for_video(
            video_tos,
            voice_id=str(voice_id or ""),
            voice_source=str(voice_source or ""),
            db=db,
            user_id=user_id,
        )
        if not voice_prep.get("ok"):
            return voice_prep
        voice_audio_url = str(voice_prep.get("audio_url") or "")
        voice_text = str(voice_prep.get("text") or "")
    final_prompt = prompt_for_mode(mode, prompt)
    # 选声音时配音是按裁剪后的原视频转写生成的，成片必须跟原视频同长，
    # 否则把 15 秒文案硬压进 5 秒会明显变速、对不上口型。
    output_seconds = int(effective_seconds) if str(voice_id or "").strip() else resolve_output_seconds(duration_seconds, effective_seconds)
    result = await submit_imitation(image_tos, video_tos, prompt=final_prompt, resolution=resolution,
                                    duration_seconds=int(output_seconds),
                                    reference_audio_url=voice_audio_url)
    if not result.get("ok"):
        return result
    result.update({"source_desc": source.get("desc") or "",
                   "source_seconds": round(float(source_seconds or 0.0), 2),
                   "sent_seconds": round(float(sent_seconds or 0.0), 2),
                   "video_seconds": int(effective_seconds),
                   "input_seconds": int(effective_seconds),
                   "output_seconds": int(output_seconds),
                   "image_url": image_tos, "video_url": video_tos,
                   "mode": normalize_mode(mode), "mode_label": mode_label(mode),
                   "resolution": normalize_resolution(result.get("resolution") or resolution),
                   "prompt": final_prompt,
                   "voice_audio_url": voice_audio_url,
                   "voice_text": voice_text})
    return result
