const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");
const share = require("../../utils/share");

const SUPER_VIDEO_PENDING_KEY = "lobster_super_video_pending_tasks";
const SUPER_VIDEO_MODEL_ID = "grok-video-3";
const SUPER_VIDEO_IMAGE_MODEL_ID = "gpt-image-2";
const SUPER_VIDEO_MAX_REFERENCES = 4;
const SUPER_VIDEO_CLAIM_MS = 5 * 60 * 1000;
const IMAGE_PENDING_KEY = "lobster_image_generate_pending_tasks";
const IMAGE_MODEL_ID = "gpt-image-2";
const IMAGE_CLAIM_MS = 5 * 60 * 1000;

function videoUrl(item) {
  return item.video_url || item.asset_video_url || item.source_video_url || "";
}

function safeUrl(url) {
  const value = String(url || "").trim();
  if (!/^https?:\/\//i.test(value)) return "";
  if (/^https?:\/\/webstatic\/?$/i.test(value)) return "";
  if (/^https?:\/\/[^/?#]+\/?$/i.test(value) && value.indexOf("webstatic") >= 0) return "";
  if (/aihuoke-1409040632\.cos\.ap-shanghai\.myqcloud\.com\/client\/miniprogram\/hifly_avatars\//i.test(value)) return "";
  return value;
}

function decodeSafe(value) {
  try {
    return decodeURIComponent(value);
  } catch (e) {
    return value;
  }
}

function hiflyStaticCover(url) {
  const decoded = decodeSafe(String(url || ""));
  const staticMatch = decoded.match(/\/static\/hifly_avatars\/([^"'&?\s#]+)/i);
  if (staticMatch && staticMatch[1]) return "";
  const hostedMatch = decoded.match(/\/client\/miniprogram\/hifly_avatars\/([^"'&?\s#]+)/i);
  if (hostedMatch && hostedMatch[1]) return "";
  const hfcdnMatch = decoded.match(/https?:\/\/hfcdn\.lingverse\.co\/[^"'&\s]+/i);
  if (hfcdnMatch) {
    return "";
  }
  return "";
}

function safeCoverUrl(url) {
  const value = String(url || "").trim();
  if (/hfcdn\.lingverse\.co/i.test(value)) return "";
  if (/aihuoke-1409040632\.cos\.ap-shanghai\.myqcloud\.com\/client\/miniprogram\/hifly_avatars\//i.test(value)) return "";
  const staticCover = hiflyStaticCover(url);
  if (staticCover) return staticCover;
  return safeUrl(url);
}

function coverUrl(item) {
  return (
    safeCoverUrl(item.cover_url) ||
    safeCoverUrl(item.image_url) ||
    safeCoverUrl(item.avatar_image_url) ||
    safeCoverUrl(item.avatar_url) ||
    safeCoverUrl(item.poster_url) ||
    safeCoverUrl(item.thumbnail_url) ||
    safeCoverUrl(item.preview_image_url) ||
    ""
  );
}

function filenameFor(item) {
  const raw = item.title || item.asset_id || item.id || "digital-human-video";
  const base = String(raw).replace(/[\\/:*?"<>|#%&=]+/g, "_").slice(0, 80) || "digital-human-video";
  return /\.mp4$/i.test(base) ? base : `${base}.mp4`;
}

function mediaProxyUrl(url, disposition, filename) {
  const token = app.globalData.token || wx.getStorageSync("lobster_token") || "";
  const params = [
    `url=${encodeURIComponent(url || "")}`,
    `disposition=${encodeURIComponent(disposition || "attachment")}`,
    `filename=${encodeURIComponent(filename || "digital-human-video.mp4")}`
  ];
  if (token) params.push(`token=${encodeURIComponent(token)}`);
  return api.buildUrl(`/api/h5-chat/media?${params.join("&")}`);
}

function statusLabel(status) {
  if (status === "success") return "已完成";
  if (status === "failed") return "失败";
  if (status === "waiting") return "等待中";
  return "生成中";
}

function cleanText(value) {
  return String(value || "").trim();
}

function canonicalUrlKey(value) {
  let url = cleanText(value);
  if (!url) return "";
  const proxyMatch = url.match(/[?&]url=([^&#]+)/);
  if (proxyMatch && proxyMatch[1]) {
    try {
      url = decodeURIComponent(proxyMatch[1]);
    } catch (err) {
      url = proxyMatch[1];
    }
  }
  return url.split("#")[0].split("?")[0];
}

function imageRowKey(item) {
  const urlKey = canonicalUrlKey(item && (item.source_url || item.url || item.preview_url || item.download_url || item.proxy_preview_url || item.proxy_download_url));
  if (urlKey) return `image:${urlKey}`;
  return "";
}

function compactKey(value) {
  return cleanText(value).replace(/\s+/g, "").toLowerCase();
}

function superVideoPromptKey(item) {
  return compactKey((item && item.prompt) || "").slice(0, 120);
}

function superVideoTitleKey(item) {
  return compactKey(item && item.title).replace(/\.(mp4|mov|webm)$/i, "");
}

function hasCompletedSuperVideoAsset(task, assetRows) {
  const taskPrompt = superVideoPromptKey(task);
  const taskTitle = superVideoTitleKey(task);
  return (assetRows || []).some((asset) => {
    const assetPrompt = superVideoPromptKey(asset);
    if (taskPrompt && assetPrompt && taskPrompt === assetPrompt) return true;
    const assetTitle = superVideoTitleKey(asset);
    return !!taskTitle && !!assetTitle && assetTitle.indexOf(taskTitle) >= 0;
  });
}

function parseJsonMaybe(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  const text = cleanText(value);
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 19);
  const pad = (num) => String(num).padStart(2, "0");
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function normalizeVideo(item) {
  const status = item.status || "processing";
  const url = videoUrl(item);
  const filename = filenameFor(item);
  return Object.assign({}, item, {
    work_type: "digital_video",
    media_type: "video",
    work_type_label: "数字人视频",
    title: item.title || "未命名视频",
    prompt: item.text || item.prompt || "",
    cover_url: coverUrl(item),
    playable_url: url,
    preview_url: url,
    download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_preview_url: url ? mediaProxyUrl(url, "inline", filename) : "",
    status,
    status_label: statusLabel(status),
    created_at_text: formatTime(item.created_at),
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed"
  });
}

function normalizeWanRoleTask(item) {
  const status = item.status || "processing";
  const url = safeUrl(item.playable_url) || safeUrl(item.video_result_url) || safeUrl(item.asset_video_url) || safeUrl(item.source_video_url) || "";
  const title = item.title || item.task_type_label || (item.task_type === "mix" ? "角色替换" : "动作迁移");
  const filename = filenameFor(Object.assign({}, item, { title }));
  return Object.assign({}, item, {
    id: `wan-${item.id || item.dashscope_task_id || url}`,
    raw_id: item.id,
    work_type: "wan_role_video",
    media_type: "video",
    work_type_label: item.task_type_label || (item.task_type === "mix" ? "角色替换" : "动作迁移"),
    title,
    prompt: item.task_type === "mix" ? "AI角色替换任务" : "AI动作迁移任务",
    cover_url: safeUrl(item.image_url),
    playable_url: url,
    preview_url: url,
    url,
    download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_preview_url: url ? mediaProxyUrl(url, "inline", filename) : "",
    status,
    status_label: item.status_label || statusLabel(status),
    created_at_text: formatTime(item.created_at),
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed",
    filename,
    billing: item.meta && item.meta.billing ? item.meta.billing : null
  });
}

function extractVideoUrl(payload) {
  const urls = [];
  const isUsableVideoUrl = (url) => {
    if (!/^https?:\/\//i.test(url)) return false;
    if (/\.(mp4|mov|webm)(\?|#|$)/i.test(url)) return true;
    return /\/(files\/video|v1\/files\/video)(\?|\/|$)/i.test(url);
  };
  const add = (value) => {
    const url = cleanText(value);
    if (!url) return;
    if (isUsableVideoUrl(url) && urls.indexOf(url) < 0) urls.push(url);
  };
  const visit = (value, depth) => {
    if (!value || depth > 7 || urls.length) return;
    if (typeof value === "string") {
      if (value[0] === "{" || value[0] === "[") {
        try {
          visit(JSON.parse(value), depth + 1);
          return;
        } catch (err) {}
      }
      add(value);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (typeof value !== "object") return;
    ["url", "video_url", "video", "file_url", "download_url", "output_url", "source_url"].forEach((key) => add(value[key]));
    Object.keys(value).forEach((key) => visit(value[key], depth + 1));
  };
  visit(payload, 0);
  return urls[0] || "";
}

function normalizeGeneratedImage(item) {
  const url = safeUrl((item && (item.url || item.source_url || item.file_url || item.image_url || item.path || item.b64_json)) || "");
  if (!url) return null;
  return {
    asset_id: cleanText((item && item.asset_id) || ""),
    url,
    source_url: url,
    preview_url: url,
    media_type: cleanText((item && item.media_type) || "image") || "image"
  };
}

function extractGeneratedImages(data) {
  const out = [];
  const visit = (obj, depth) => {
    if (!obj || depth > 5) return;
    if (typeof obj === "string") {
      const parsed = parseJsonMaybe(obj);
      if (parsed) visit(parsed, depth + 1);
      return;
    }
    if (Array.isArray(obj)) {
      obj.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (typeof obj !== "object") return;
    const saved = obj.saved_assets || (obj.result && obj.result.saved_assets);
    if (Array.isArray(saved)) saved.forEach((item) => {
      const normalized = normalizeGeneratedImage(item);
      if (normalized) out.push(normalized);
    });
    const images = obj.output && Array.isArray(obj.output.images) ? obj.output.images : [];
    images.forEach((item) => {
      const normalized = normalizeGeneratedImage(item);
      if (normalized) out.push(normalized);
    });
    const mediaUrls = Array.isArray(obj.media_urls) ? obj.media_urls : [];
    mediaUrls.forEach((url) => {
      const normalized = normalizeGeneratedImage({ url, media_type: "image" });
      if (normalized) out.push(normalized);
    });
    const dataImages = Array.isArray(obj.data) ? obj.data : [];
    dataImages.forEach((item) => {
      const normalized = normalizeGeneratedImage(item);
      if (normalized) out.push(normalized);
    });
    const single = normalizeGeneratedImage(obj);
    if (single && (obj.url || obj.image_url || obj.file_url || obj.b64_json)) out.push(single);
  };
  visit(data, 0);
  return mergeMediaRows(out).filter((item) => item.media_type === "image" || !item.media_type);
}

function isTerminalFailure(payload) {
  const text = JSON.stringify(payload || {}).toLowerCase();
  return text.indexOf("failed") >= 0 || text.indexOf("failure") >= 0 || text.indexOf("error") >= 0 || text.indexOf("cancel") >= 0;
}

function pendingSuperVideoTasks() {
  const rows = wx.getStorageSync(SUPER_VIDEO_PENDING_KEY);
  const now = Date.now();
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((item) => item && item.task_id && item.status !== "success")
    .filter((item) => now - Number(item.created_at_ms || 0) < 24 * 60 * 60 * 1000);
}

function setPendingSuperVideoTasks(rows) {
  wx.setStorageSync(SUPER_VIDEO_PENDING_KEY, (rows || []).filter((item) => item && item.task_id).slice(0, 50));
}

function pendingImageTasks() {
  const rows = wx.getStorageSync(IMAGE_PENDING_KEY);
  const now = Date.now();
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((item) => item && item.task_id && item.status !== "success")
    .filter((item) => now - Number(item.created_at_ms || 0) < 24 * 60 * 60 * 1000);
}

function setPendingImageTasks(rows) {
  wx.setStorageSync(IMAGE_PENDING_KEY, (rows || []).filter((item) => item && item.task_id).slice(0, 50));
}

function replacePendingImageTask(oldTaskId, task) {
  const rows = pendingImageTasks();
  const kept = rows.filter((item) => item && item.task_id !== oldTaskId && item.task_id !== (task && task.task_id));
  const next = task && task.task_id ? [task].concat(kept).slice(0, 50) : kept.slice(0, 50);
  setPendingImageTasks(next);
}

function pruneCompletedSuperVideoPendingTasks(assetRows) {
  const rows = pendingSuperVideoTasks();
  const next = rows.filter((task) => !hasCompletedSuperVideoAsset(task, assetRows));
  if (next.length !== rows.length) setPendingSuperVideoTasks(next);
  return next;
}

function replacePendingSuperVideoTask(oldTaskId, task) {
  const rows = wx.getStorageSync(SUPER_VIDEO_PENDING_KEY);
  const kept = Array.isArray(rows)
    ? rows.filter((item) => item && item.task_id !== oldTaskId && item.task_id !== (task && task.task_id))
    : [];
  const next = task && task.task_id ? [task].concat(kept).slice(0, 50) : kept.slice(0, 50);
  setPendingSuperVideoTasks(next);
}

function normalizeOpenMindPendingTask(item) {
  const status = item.status || "processing";
  const url = safeUrl(item.playable_url || item.url || "");
  const title = item.title || "AI视频获客";
  const filename = filenameFor(Object.assign({}, item, { title }));
  const processingLabel = status === "preparing_image" ? "生成参考图中" : status === "submitting_video" ? "提交视频中" : "";
  return Object.assign({}, item, {
    id: item.id || `openmind-${item.task_id}`,
    work_type: "openmind_video",
    media_type: "video",
    work_type_label: "AI视频获客",
    title,
    prompt: item.prompt || "AI视频生成任务",
    cover_url: safeUrl(item.cover_url),
    playable_url: url,
    preview_url: url,
    url,
    download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_preview_url: url ? mediaProxyUrl(url, "inline", filename) : "",
    status,
    status_label: item.status_label || processingLabel || statusLabel(status),
    created_at_text: formatTime(item.created_at || item.created_at_ms),
    is_processing: status === "processing" || status === "waiting" || status === "preparing_image" || status === "submitting_video",
    is_success: status === "success",
    is_failed: status === "failed",
    filename
  });
}

function normalizePendingImageTask(item) {
  const status = item.status || "processing";
  return Object.assign({}, item, {
    id: item.id || `pending-${item.task_id}`,
    work_type: "image_generate",
    media_type: "image",
    work_type_label: "AI图片",
    title: item.title || "AI图片生成",
    prompt: item.prompt || "",
    preview_url: "",
    url: "",
    download_url: "",
    status,
    status_label: item.status_label || statusLabel(status),
    created_at_text: formatTime(item.created_at || item.created_at_ms),
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed"
  });
}

function filenameFromUrl(url) {
  const path = String(url || "").split("?")[0].split("#")[0];
  const name = decodeURIComponent(path.split("/").pop() || "");
  return name || "AI图片";
}

function looksLikeStorageFilename(value) {
  return /^(assets|uploads|temp_assets)\//i.test(String(value || "")) || /^[a-f0-9]{8,}\.(mp4|mov|webm|jpg|png|jpeg)$/i.test(String(value || ""));
}

function normalizeMediaItem(item) {
  const url = safeUrl(item.url) || safeUrl(item.preview_url) || safeUrl(item.download_url) || "";
  const mediaType = item.media_type || "media";
  const rawTitle = item.title || item.prompt || "";
  const rawFilename = item.filename || filenameFromUrl(url);
  const title = rawTitle && !looksLikeStorageFilename(rawTitle) ? rawTitle : (mediaType === "video" ? "AI视频结果" : rawFilename);
  const filename = filenameFromUrl(url || title);
  const draft = item.publish_draft && typeof item.publish_draft === "object" ? item.publish_draft : null;
  const draftStatus = String((draft && draft.status) || "").toLowerCase();
  return Object.assign({}, item, {
    id: item.id || item.asset_id || url,
    run_id: item.run_id || (draft && draft.run_id) || "",
    publish_draft: draft,
    publish_status: draftStatus,
    publish_status_label: publishStatusLabel(draftStatus),
    can_publish: !!draft && !["published", "pending", "processing"].includes(draftStatus),
    publish_target_label: draft ? [draft.platform_name || draft.platform, draft.account_nickname || draft.account_id].filter(Boolean).join(" · ") : "",
    media_type: mediaType,
    work_type: mediaType,
    work_type_label: mediaType === "image" ? "AI图片" : mediaType === "video" ? "AI视频" : mediaType === "audio" ? "AI音频" : "AI素材",
    title,
    prompt: item.prompt || "",
    playable_url: url,
    preview_url: safeUrl(item.preview_url) || url,
    download_url: safeUrl(item.download_url) || url,
    proxy_download_url: safeUrl(item.proxy_download_url) || safeUrl(item.download_url) || url,
    proxy_preview_url: safeUrl(item.proxy_preview_url) || safeUrl(item.preview_url) || url,
    status: "success",
    status_label: "已完成",
    created_at_text: formatTime(item.created_at),
    filename
  });
}

function cacheRecentImageAssets(rows) {
  const current = wx.getStorageSync("lobster_recent_image_assets") || [];
  const merged = mergeMediaRows((rows || []).concat(Array.isArray(current) ? current : []))
    .filter((item) => item && (item.url || item.source_url || item.preview_url))
    .slice(0, 20);
  wx.setStorageSync("lobster_recent_image_assets", merged);
}

function saveGeneratedImageAssets(rows, prompt) {
  const tasks = mergeMediaRows(rows || []).map((item) => {
    const url = safeUrl(item.url || item.source_url || item.preview_url);
    if (!url) return Promise.resolve(null);
    if (item.saved || item.asset_id) return Promise.resolve(Object.assign({}, item, { url, saved: item.saved !== false }));
    return app.request({
      method: "POST",
      url: "/api/assets/save-url",
      data: {
        url,
        media_type: "image",
        tags: "auto,image_generate,miniprogram",
        prompt,
        model: IMAGE_MODEL_ID
      },
      timeout: 180000
    }).then((saved) => ({
      asset_id: String((saved && saved.asset_id) || ""),
      url: String((saved && (saved.source_url || saved.url)) || url),
      source_url: String((saved && (saved.source_url || saved.url)) || url),
      preview_url: String((saved && (saved.preview_url || saved.source_url || saved.url)) || url),
      media_type: "image",
      saved: true
    })).catch(() => Object.assign({}, item, { url, saved: false }));
  });
  return Promise.all(tasks).then((items) => mergeMediaRows(items.filter(Boolean)));
}

function imagePromptKey(item) {
  return compactKey((item && item.prompt) || "").slice(0, 120);
}

function hasCompletedImageAsset(task, assetRows) {
  const taskPrompt = imagePromptKey(task);
  if (!taskPrompt) return false;
  return (assetRows || []).some((asset) => {
    const assetPrompt = imagePromptKey(asset);
    return !!assetPrompt && assetPrompt === taskPrompt;
  });
}

function pruneCompletedImagePendingTasks(assetRows) {
  const rows = pendingImageTasks();
  const next = rows.filter((task) => !hasCompletedImageAsset(task, assetRows));
  if (next.length !== rows.length) setPendingImageTasks(next);
  return next;
}

function hasRunnableImageTask() {
  const now = Date.now();
  return pendingImageTasks().some((task) => task.status !== "failed" && Number(task.claim_until_ms || 0) <= now);
}

function publishStatusLabel(status) {
  const s = String(status || "").toLowerCase();
  return {
    ready: "待发布",
    draft: "待发布",
    pending: "等待发布",
    processing: "发布中",
    published: "已发布",
    failed: "发布失败"
  }[s] || "";
}

function normalizeAssetItem(item) {
  const url = safeUrl(item.source_url) || safeUrl(item.url) || "";
  return normalizeMediaItem(Object.assign({}, item, {
    id: item.id || item.asset_id || url,
    title: item.filename || item.title || filenameFromUrl(url),
    url,
    preview_url: safeUrl(item.preview_url) || url,
    download_url: safeUrl(item.download_url) || url,
    proxy_preview_url: safeUrl(item.proxy_preview_url) || url,
    proxy_download_url: safeUrl(item.proxy_download_url) || url,
    media_type: item.media_type || "media"
  }));
}

function mergeMediaRows(rows) {
  const seen = {};
  const out = [];
  (rows || []).forEach((item) => {
    if (!item) return;
    const key = imageRowKey(item) || String(item.asset_id || item.id || item.playable_url || item.url || item.source_url || "").trim();
    if (!key || seen[key]) return;
    seen[key] = true;
    out.push(item);
  });
  return out;
}

function isHiflyVideoAsset(item) {
  const tags = String((item && item.tags) || "").toLowerCase();
  const meta = item && item.meta ? JSON.stringify(item.meta).toLowerCase() : "";
  return tags.indexOf("hifly") >= 0 || tags.indexOf("video_tts") >= 0 || meta.indexOf("hifly") >= 0;
}

function isInputVideoAsset(item) {
  const tags = String((item && item.tags) || "").toLowerCase();
  const meta = item && item.meta ? JSON.stringify(item.meta).toLowerCase() : "";
  if (tags.indexOf("input") >= 0) return true;
  if (tags.indexOf("role_transfer/input") >= 0) return true;
  if (meta.indexOf("role_transfer/input") >= 0) return true;
  if (meta.indexOf('"input"') >= 0 && meta.indexOf("role_transfer") >= 0) return true;
  return false;
}

function isGeneratedVideoAsset(item) {
  const tags = String((item && item.tags) || "").toLowerCase();
  const meta = item && item.meta ? JSON.stringify(item.meta).toLowerCase() : "";
  if (isInputVideoAsset(item)) return false;
  if (tags.indexOf("role_transfer") >= 0 || meta.indexOf("wan_role_task_id") >= 0) return true;
  if (tags.indexOf("video_inspiration") >= 0 || meta.indexOf("video_inspiration") >= 0) return true;
  if (tags.indexOf("auto") >= 0 || tags.indexOf("generated") >= 0 || tags.indexOf("result") >= 0) return true;
  return false;
}

Page({
  data: {
    phoneBound: false,
    authPanelVisible: false,
    authHint: "查看作品前需要快捷登录并绑定手机号。",
    mediaTab: "video",
    videoKind: "digital",
    loading: false,
    polling: false,
    works: [],
    mediaWorks: [],
    previewVisible: false,
    previewItem: null,
    previewVideoUrl: "",
    onlineText: ""
  },

  pollTimer: null,
  savingOpenMindTasks: {},
  claimingOpenMindTasks: {},
  claimingImageTasks: {},

  onShow() {
    share.showShareMenu();
    app.restoreSession();
    this.refreshAuthState();
    const rememberedMediaTab = wx.getStorageSync("lobster_downloads_media_tab");
    const rememberedVideoKind = wx.getStorageSync("lobster_downloads_video_kind");
    if (rememberedMediaTab && ["video", "image", "text", "audio"].indexOf(rememberedMediaTab) >= 0) {
      this.setData({ mediaTab: rememberedMediaTab });
    }
    if (rememberedVideoKind && ["digital", "super"].indexOf(rememberedVideoKind) >= 0) {
      this.setData({ videoKind: rememberedVideoKind });
    }
    const openSuperVideo = wx.getStorageSync("lobster_open_super_video");
    if (openSuperVideo) {
      wx.removeStorageSync("lobster_open_super_video");
      this.setData({ mediaTab: "video", videoKind: "super" });
      wx.setStorageSync("lobster_downloads_media_tab", "video");
      wx.setStorageSync("lobster_downloads_video_kind", "super");
    }
    const openMediaTab = wx.getStorageSync("lobster_open_media_tab");
    if (openMediaTab) {
      wx.removeStorageSync("lobster_open_media_tab");
      const nextTab = openMediaTab === "image" ? "image" : this.data.mediaTab;
      this.setData({ mediaTab: nextTab });
      wx.setStorageSync("lobster_downloads_media_tab", nextTab);
    }
    const shouldRefresh = wx.getStorageSync("lobster_refresh_works");
    if (shouldRefresh) wx.removeStorageSync("lobster_refresh_works");
    if (this.data.phoneBound) this.loadWorks();
  },

  onHide() {
    this.stopPolling();
  },

  onUnload() {
    this.stopPolling();
  },

  onPullDownRefresh() {
    this.loadWorks().finally(() => wx.stopPullDownRefresh());
  },

  refreshAuthState() {
    const phoneBound = Boolean(app.globalData.token && app.globalData.phone);
    this.setData({ phoneBound });
    return phoneBound;
  },

  showAuthPanel(hint) {
    this.refreshAuthState();
    if (this.data.phoneBound) return false;
    this.setData({
      authPanelVisible: true,
      authHint: hint || "查看作品前需要快捷登录并绑定手机号。"
    });
    return true;
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then((data) => {
        this.refreshAuthState();
        if (data.needs_phone_bind || !app.globalData.phone) {
          wx.showToast({ title: "请手机号快捷登录", icon: "none" });
          return;
        }
        this.setData({ authPanelVisible: false });
        this.loadWorks();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      wx.showToast({ title: "快捷验证失败", icon: "none" });
      return;
    }
    const bind = () => this.bindPhone(code);
    if (!app.globalData.token) {
      app.loginWithWechat().then(bind).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    bind();
  },

  bindPhone(code) {
    wx.showLoading({ title: "绑定中", mask: true });
    app
      .bindPhone(code)
      .then(() => {
        this.setData({ authPanelVisible: false, phoneBound: true });
        wx.showToast({ title: "绑定成功", icon: "success" });
        this.loadWorks();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  setMediaTab(evt) {
    const tab = evt.currentTarget.dataset.tab || "video";
    this.setData({ mediaTab: tab });
    wx.setStorageSync("lobster_downloads_media_tab", tab);
    if (this.data.phoneBound) this.loadWorks();
  },

  setVideoKind(evt) {
    const kind = evt.currentTarget.dataset.kind || "digital";
    this.setData({ videoKind: kind });
    wx.setStorageSync("lobster_downloads_video_kind", kind);
    if (this.data.phoneBound) this.loadWorks();
  },

  noop() {},

  loadWorks() {
    if (!this.refreshAuthState()) {
      this.setData({ works: [], mediaWorks: [], loading: false });
      return Promise.resolve();
    }
    if (this.data.mediaTab !== "video") return this.loadMediaWorks(this.data.mediaTab);
    if (this.data.videoKind !== "digital") return this.loadAssetVideos();
    this.setData({ loading: true });
    return app
      .request({ url: "/api/hifly/my/video/list?page=1&size=50" })
      .then((data) => {
        const works = (data.items || []).map(normalizeVideo);
        this.setData({ works, authPanelVisible: false });
        this.refreshPolling();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
  },

  loadAssetVideos() {
    this.stopPolling();
    this.setData({ loading: true });
    return Promise.all([
      app.request({ url: "/api/wan/role-transfer/tasks?page=1&size=30&refresh=true" }).catch(() => ({ items: [] })),
      app.request({ url: "/api/assets?media_type=video&limit=80" }).catch(() => ({ assets: [] }))
    ])
      .then(([taskData, assetData]) => {
        const taskRows = (taskData.items || []).map(normalizeWanRoleTask);
        const taskAssetIds = {};
        taskRows.forEach((item) => {
          if (item.asset_id) taskAssetIds[String(item.asset_id)] = true;
        });
        const assetRows = (assetData.assets || [])
          .filter((item) => item && item.source_url && item.media_type === "video" && !isHiflyVideoAsset(item))
          .filter((item) => isGeneratedVideoAsset(item))
          .filter((item) => !item.asset_id || !taskAssetIds[String(item.asset_id)])
          .map(normalizeAssetItem);
        const pendingRows = pruneCompletedSuperVideoPendingTasks(assetRows).map(normalizeOpenMindPendingTask);
        const rows = mergeMediaRows(assetRows.concat(pendingRows).concat(taskRows));
        this.setData({ mediaWorks: rows, authPanelVisible: false });
        this.refreshSuperVideoPolling();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
  },

  loadMediaWorks(mediaType) {
    const type = mediaType || this.data.mediaTab || "image";
    if (type === "text") {
      this.setData({ mediaWorks: [], loading: false });
      return Promise.resolve();
    }
    const deviceId = app.globalData.deviceId || wx.getStorageSync("lobster_device_id") || "";
    if (!deviceId) {
      wx.showToast({ title: "未找到当前手机设备", icon: "none" });
      return Promise.resolve();
    }
    this.stopPolling();
    this.setData({ loading: true });
    const mobileReq = app.request({ url: `/api/mobile/downloads?device_id=${encodeURIComponent(deviceId)}&media_type=${encodeURIComponent(type)}&limit=80` });
    const assetReq = type === "image"
      ? app.request({ url: "/api/assets?media_type=image&limit=80" }).catch(() => ({ assets: [] }))
      : Promise.resolve({ assets: [] });
    return Promise.all([mobileReq, assetReq])
      .then(([data, assetData]) => {
        const mobileRows = (data.items || []).map(normalizeMediaItem).filter((item) => item.media_type === type);
        const assetRows = (assetData.assets || [])
          .filter((item) => item && item.source_url && item.media_type === type)
          .map(normalizeAssetItem)
          .filter((item) => item.media_type === type);
        const cachedRows = type === "image"
          ? (wx.getStorageSync("lobster_recent_image_assets") || []).map(normalizeMediaItem).filter((item) => item.media_type === type)
          : [];
        const pendingRows = type === "image" ? pruneCompletedImagePendingTasks(assetRows.concat(cachedRows).concat(mobileRows)).map(normalizePendingImageTask) : [];
        const rows = mergeMediaRows(pendingRows.concat(cachedRows).concat(mobileRows).concat(assetRows));
        if (type === "image") {
          console.log("[downloads] image works loaded", {
            mobile: mobileRows.length,
            assets: assetRows.length,
            cached: cachedRows.length,
            pending: pendingRows.length,
            total: rows.length
          });
        }
        this.setData({ mediaWorks: rows, authPanelVisible: false });
        if (type === "image") this.refreshImagePolling();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
  },

  refreshPolling() {
    const hasProcessing = this.data.works.some((item) => item.is_processing && item.task_id);
    if (hasProcessing) {
      this.startPolling();
      return;
    }
    this.stopPolling();
  },

  refreshSuperVideoPolling() {
    const hasProcessing = this.data.mediaTab === "video" && this.data.videoKind === "super" && this.data.mediaWorks.some((item) => item.is_processing);
    if (hasProcessing) {
      this.startPolling();
      return;
    }
    this.stopPolling();
  },

  refreshImagePolling() {
    const hasProcessing = this.data.mediaTab === "image" && this.data.mediaWorks.some((item) => item.is_processing);
    if (hasProcessing) {
      this.startPolling();
      if (hasRunnableImageTask()) setTimeout(() => this.pollProcessingWorks(), 50);
      return;
    }
    this.stopPolling();
  },

  startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => this.pollProcessingWorks(), 8000);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  pollProcessingWorks() {
    if (this.data.mediaTab === "video" && this.data.videoKind === "super") {
      if (this.data.polling) return;
      this.setData({ polling: true });
      this.pollOpenMindVideoTasks()
        .then(() => this.loadAssetVideos())
        .finally(() => this.setData({ polling: false }));
      return;
    }
    if (this.data.mediaTab === "image") {
      if (this.data.polling) return;
      this.setData({ polling: true });
      this.pollImageGenerateTasks()
        .then(() => this.loadMediaWorks("image"))
        .finally(() => this.setData({ polling: false }));
      return;
    }
    const targets = this.data.works.filter((item) => item.is_processing && item.task_id).slice(0, 5);
    if (!targets.length || this.data.polling) return;
    this.setData({ polling: true });
    Promise.all(
      targets.map((item) =>
        app
          .request({
            method: "POST",
            url: "/api/hifly/my/video/task",
            data: { task_id: item.task_id },
            timeout: 60000
          })
          .then((data) => normalizeVideo(data.item || item))
          .catch(() => item)
      )
    )
      .then((updated) => {
        const byId = {};
        updated.forEach((item) => {
          byId[String(item.id)] = item;
        });
        const works = this.data.works.map((item) => byId[String(item.id)] || item);
        this.setData({ works });
        this.refreshPolling();
      })
      .finally(() => this.setData({ polling: false }));
  },

  pollOpenMindVideoTasks() {
    const tasks = pendingSuperVideoTasks();
    if (!tasks.length) return Promise.resolve();
    const localTasks = tasks.filter((task) => task.local_only);
    const remoteTasks = tasks.filter((task) => !task.local_only);
    return Promise.all(
      localTasks.slice(0, 2).map((task) => this.processLocalSuperVideoTask(task))
        .concat(remoteTasks.slice(0, 5).map((task) =>
        app
          .request({
            url: `/api/comfly-proxy/openmind/v1/videos/${encodeURIComponent(task.task_id)}`,
            timeout: 60000
          })
          .then((data) => {
            const url = extractVideoUrl(data);
            if (url) {
              return this.saveOpenMindVideoTask(task, url, data).then((saved) => {
                if (saved) {
                  replacePendingSuperVideoTask(task.task_id, null);
                  return null;
                }
                return Object.assign({}, task, {
                  status: "waiting",
                  status_label: "保存中",
                  playable_url: url,
                  preview_url: url,
                  url
                });
              });
            }
            if (isTerminalFailure(data)) {
              return Object.assign({}, task, {
                status: "failed",
                status_label: "失败",
                error_message: "生成失败，请重新提交"
              });
            }
            return task;
          })
          .catch(() => task)
      ))
    ).then((updated) => {
      const byId = {};
      updated.filter(Boolean).forEach((item) => {
        byId[String(item.task_id)] = item;
      });
      const next = pendingSuperVideoTasks().map((item) => byId[String(item.task_id)] || item);
      setPendingSuperVideoTasks(next.filter(Boolean));
    });
  },

  processLocalSuperVideoTask(task) {
    if (!task || !task.task_id) return Promise.resolve(task);
    if (Number(task.claim_until_ms || 0) > Date.now()) return Promise.resolve(task);
    if (this.claimingOpenMindTasks[task.task_id]) return this.claimingOpenMindTasks[task.task_id];
    replacePendingSuperVideoTask(task.task_id, Object.assign({}, task, {
      claim_owner: "downloads",
      claim_until_ms: Date.now() + SUPER_VIDEO_CLAIM_MS
    }));
    this.claimingOpenMindTasks[task.task_id] = this.ensureSuperVideoReferenceUrls(task)
      .then((refs) => this.submitSuperVideoTaskFromWorks(task, refs))
      .catch((err) => Object.assign({}, task, {
        status: "failed",
        status_label: "失败",
        error_message: api.errorMessage(err) || "提交失败，请重新提交"
      }))
      .finally(() => {
        delete this.claimingOpenMindTasks[task.task_id];
      });
    return this.claimingOpenMindTasks[task.task_id];
  },

  ensureSuperVideoReferenceUrls(task) {
    const refs = (task.referenceImages || [])
      .map((item) => safeUrl(item.source_url || item.url))
      .filter(Boolean)
      .slice(0, SUPER_VIDEO_MAX_REFERENCES);
    if (refs.length) return Promise.resolve(refs);
    const ratio = task.ratio === "16:9" ? "16:9" : "9:16";
    const imagePrompt = `${task.prompt || ""}\n生成一张适合作为图生视频首帧的高清画面，主体清晰，画面干净，构图适合${ratio}视频，不要文字水印。`;
    return app
      .request({
        method: "POST",
        url: "/api/comfly-proxy/v1/images/generations",
        data: {
          model: SUPER_VIDEO_IMAGE_MODEL_ID,
          prompt: imagePrompt,
          image_size: ratio,
          aspect_ratio: ratio,
          ratio,
          num_images: 1,
          n: 1,
          response_format: "url",
          source: "miniprogram_video_inspiration_reference"
        },
        timeout: 240000
      })
      .then((data) => {
        const images = extractGeneratedImages(data);
        const first = images[0];
        const url = first && safeUrl(first.source_url || first.url);
        if (!url) throw new Error("参考图生成失败，请重新提交");
        const generatedRef = {
          asset_id: first.asset_id || "",
          url,
          source_url: url,
          preview_url: url,
          media_type: "image",
          generated: true
        };
        replacePendingSuperVideoTask(task.task_id, Object.assign({}, task, {
          status: "submitting_video",
          status_label: "提交视频中",
          cover_url: url,
          referenceImages: [generatedRef]
        }));
        wx.setStorageSync("lobster_refresh_works", "1");
        return [url];
      });
  },

  submitSuperVideoTaskFromWorks(task, referenceUrls) {
    const refs = (referenceUrls || []).filter(Boolean).slice(0, SUPER_VIDEO_MAX_REFERENCES);
    if (!refs.length) return Promise.reject(new Error("缺少视频参考图"));
    const ratio = task.ratio === "16:9" ? "16:9" : "9:16";
    const deviceId = app.globalData.deviceId || wx.getStorageSync("lobster_device_id") || "";
    const phone = app.globalData.phone || wx.getStorageSync("lobster_phone") || "";
    return app
      .request({
        method: "POST",
        url: "/api/comfly-proxy/openmind/v1/videos",
        data: {
          model: SUPER_VIDEO_MODEL_ID,
          prompt: task.prompt || "",
          aspect_ratio: ratio,
          ratio,
          duration: task.duration || 8,
          seconds: task.duration || 8,
          resolution: "720p",
          size: ratio === "16:9" ? "1280x720" : "720x1280",
          count: task.count || 1,
          n: task.count || 1,
          images: refs,
          image_url: refs[0] || "",
          image: refs[0] || "",
          reference_image_urls: refs,
          device_id: deviceId,
          phone,
          source: "miniprogram_video_inspiration",
          title: task.title || "获客灵感"
        },
        timeout: 180000
      })
      .then((data) => {
        const directUrl = extractVideoUrl(data);
        if (directUrl) {
          return this.saveOpenMindVideoTask(task, directUrl, data).then((saved) => {
            if (saved) {
              replacePendingSuperVideoTask(task.task_id, null);
              return null;
            }
            return Object.assign({}, task, {
              status: "waiting",
              status_label: "保存中",
              playable_url: directUrl,
              preview_url: directUrl,
              url: directUrl,
              local_only: false
            });
          });
        }
        const taskId = cleanText(data && (data.id || data.task_id || data.video_id || data.job_id || data.request_id || data.generation_id || data.run_id));
        const nestedTaskId = cleanText(data && data.data && (data.data.id || data.data.task_id || data.data.video_id || data.data.job_id || data.data.request_id || data.data.generation_id || data.data.run_id));
        const realTaskId = taskId || nestedTaskId;
        if (!realTaskId) throw new Error("任务提交成功但没有返回任务ID");
        const next = Object.assign({}, task, {
          id: `openmind-${realTaskId}`,
          task_id: realTaskId,
          provider: "openmind",
          local_only: false,
          status: "processing",
          status_label: "生成中",
          cover_url: task.cover_url || refs[0],
          referenceImages: task.referenceImages && task.referenceImages.length ? task.referenceImages : refs.map((url) => ({ url, source_url: url, preview_url: url, media_type: "image" })),
          submit_payload: data || null
        });
        replacePendingSuperVideoTask(task.task_id, next);
        return next;
      });
  },

  saveOpenMindVideoTask(task, url, payload) {
    if (!task || !task.task_id || !url) return Promise.resolve();
    if (this.savingOpenMindTasks[task.task_id]) return this.savingOpenMindTasks[task.task_id];
    this.savingOpenMindTasks[task.task_id] = app
      .request({
        method: "POST",
        url: "/api/assets/save-url",
        data: {
          url,
          media_type: "video",
          tags: "auto,video_inspiration,miniprogram",
          prompt: task.prompt || "",
          model: task.model || "grok-video-3",
          name: `${task.title || "AI视频获客"}-${Date.now()}.mp4`
        },
        timeout: 180000
      })
      .then(() => {
        wx.setStorageSync("lobster_refresh_works", "1");
        return true;
      })
      .catch(() => false)
      .finally(() => {
        delete this.savingOpenMindTasks[task.task_id];
      });
    return this.savingOpenMindTasks[task.task_id];
  },

  pollImageGenerateTasks() {
    const tasks = pendingImageTasks();
    if (!tasks.length) return Promise.resolve();
    return Promise.all(tasks.slice(0, 2).map((task) => this.processLocalImageTask(task))).then((updated) => {
      const byId = {};
      updated.filter(Boolean).forEach((item) => {
        byId[String(item.task_id)] = item;
      });
      const next = pendingImageTasks().map((item) => byId[String(item.task_id)] || item);
      setPendingImageTasks(next.filter(Boolean));
    });
  },

  processLocalImageTask(task) {
    if (!task || !task.task_id || !task.payload) return Promise.resolve(task);
    if (task.status === "failed") return Promise.resolve(task);
    if (Number(task.claim_until_ms || 0) > Date.now()) return Promise.resolve(task);
    if (this.claimingImageTasks[task.task_id]) return this.claimingImageTasks[task.task_id];
    replacePendingImageTask(task.task_id, Object.assign({}, task, {
      claim_owner: "downloads",
      claim_until_ms: Date.now() + IMAGE_CLAIM_MS
    }));
    this.claimingImageTasks[task.task_id] = app
      .request({
        method: "POST",
        url: "/api/comfly-proxy/v1/images/generations",
        data: task.payload,
        timeout: 240000
      })
      .then((data) => {
        const images = extractGeneratedImages(data);
        if (!images.length) throw new Error("图片生成失败，请重新提交");
        return saveGeneratedImageAssets(images, task.prompt || "").then((assets) => {
          cacheRecentImageAssets(assets);
          replacePendingImageTask(task.task_id, null);
          wx.setStorageSync("lobster_refresh_works", "1");
          return null;
        });
      })
      .catch((err) => Object.assign({}, task, {
        status: "failed",
        status_label: "失败",
        error_message: api.errorMessage(err) || "生成失败，请重新提交",
        claim_until_ms: 0
      }))
      .finally(() => {
        delete this.claimingImageTasks[task.task_id];
      });
    return this.claimingImageTasks[task.task_id];
  },

  openWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item) return;
    if (item.is_processing) {
      wx.showToast({ title: "生成中，可稍后刷新", icon: "none" });
      return;
    }
    const url = safeUrl(item.proxy_preview_url) || safeUrl(item.preview_url) || safeUrl(item.playable_url);
    if (!url) {
      wx.showToast({ title: item.is_failed ? "生成失败，请重新提交" : "暂无可播放视频", icon: "none" });
      return;
    }
    this.setData({
      previewVisible: true,
      previewItem: item,
      previewVideoUrl: url
    });
  },

  copyPrompt(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item || !item.prompt) return;
    wx.setClipboardData({ data: item.prompt });
  },

  deleteWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item || !item.id) return;
    wx.showModal({
      title: "删除作品",
      content: "删除后作品记录不可恢复。",
      confirmText: "删除",
      confirmColor: "#ef4444",
      success: (res) => {
        if (!res.confirm) return;
        app
          .request({ method: "DELETE", url: `/api/hifly/my/video/${item.id}` })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadWorks();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  },

  saveWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = evt.currentTarget.dataset.source === "preview" ? this.data.previewItem : this.data.works[index];
    if (!item || !item.playable_url) {
      wx.showToast({ title: "视频还未生成", icon: "none" });
      return;
    }
    media
      .saveToAlbum({
        id: item.id,
        title: item.title,
        media_type: "video",
        url: item.playable_url,
        preview_url: item.playable_url,
        download_url: item.download_url,
        proxy_download_url: item.proxy_download_url,
        proxy_preview_url: item.proxy_preview_url
      })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const reason = api.errorMessage(err);
        media.copyLink(item.playable_url).finally(() => wx.showToast({ title: `保存失败: ${reason}`.slice(0, 28), icon: "none" }));
      });
  },

  saveMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = evt.currentTarget.dataset.source === "preview" ? this.data.previewItem : this.data.mediaWorks[index];
    if (!item || !item.download_url) {
      wx.showToast({ title: item && item.is_processing ? "生成中，预计5-10分钟" : "暂无可保存链接", icon: "none" });
      return;
    }
    media
      .saveToAlbum(item)
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const reason = api.errorMessage(err);
        media.copyLink(item.url || item.download_url || "").finally(() => wx.showToast({ title: `保存失败: ${reason}`.slice(0, 28), icon: "none" }));
      });
  },

  openMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    if (!item) return;
    if (item.media_type === "image") {
      if (item.is_processing) {
        wx.showToast({ title: "生成中，预计1-2分钟", icon: "none" });
        return;
      }
      if (item.is_failed) {
        wx.showToast({ title: "生成失败，请重新提交", icon: "none" });
        return;
      }
      this.previewMediaWork(evt);
      return;
    }
    if (item.media_type !== "video") return;
    if (item.is_processing) {
      wx.showToast({ title: "生成中，预计5-10分钟", icon: "none" });
      return;
    }
    const url = safeUrl(item.proxy_preview_url) || safeUrl(item.preview_url) || safeUrl(item.url) || safeUrl(item.playable_url);
    if (!url) {
      wx.showToast({ title: item.is_failed ? "生成失败，请重新提交" : "暂无可播放视频", icon: "none" });
      return;
    }
    this.setData({
      previewVisible: true,
      previewItem: item,
      previewVideoUrl: url
    });
  },

  closePreview() {
    this.setData({
      previewVisible: false,
      previewItem: null,
      previewVideoUrl: ""
    });
  },

  savePreviewWork() {
    const item = this.data.previewItem;
    if (!item) return;
    if (item.work_type === "digital_video") {
      this.saveWork({ currentTarget: { dataset: { source: "preview" } } });
      return;
    }
    this.saveMediaWork({ currentTarget: { dataset: { source: "preview" } } });
  },

  copyMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    const url = item && (item.url || item.download_url || item.preview_url);
    if (!url) {
      wx.showToast({ title: item && item.is_processing ? "生成中，预计5-10分钟" : "暂无链接", icon: "none" });
      return;
    }
    media.copyLink(url).then(() => wx.showToast({ title: "链接已复制", icon: "success" }));
  },

  publishMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    const runId = item && (item.run_id || (item.publish_draft && item.publish_draft.run_id));
    if (!runId) {
      wx.showToast({ title: "缺少发布记录", icon: "none" });
      return;
    }
    wx.showLoading({ title: "提交发布", mask: true });
    app
      .request({
        method: "POST",
        url: `/api/scheduled-tasks/runs/${encodeURIComponent(runId)}/publish-request`,
        data: {}
      })
      .then(() => {
        wx.showToast({ title: "已提交发布", icon: "success" });
        this.loadMediaWorks(this.data.mediaTab);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  previewMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    const url = item && (safeUrl(item.preview_url) || safeUrl(item.url));
    if (!url) return;
    if (item.media_type === "image") {
      wx.previewImage({
        current: url,
        urls: this.data.mediaWorks.filter((row) => row.media_type === "image").map((row) => row.preview_url || row.url).filter(Boolean)
      });
    }
  },

  goCreate() {
    wx.navigateTo({ url: "/pages/digital/digital" });
  },

  onShareAppMessage() {
    const item = this.data.previewItem;
    if (this.data.previewVisible && item) {
      return share.appShare({
        title: item.title || "我的AI视频作品",
        path: "/pages/downloads/downloads",
        imageUrl: item.cover_url || ""
      });
    }
    return share.appShare({
      title: "我的AI作品 - 必火AI员工",
      path: "/pages/downloads/downloads"
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "必火AI员工 - AI视频作品"
    });
  }
});
