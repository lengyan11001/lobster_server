const staticAssets = require("./static_assets");

const DEFAULT_TITLE = "必火AI员工";
const DEFAULT_IMAGE = staticAssets.staticAssetUrl("share-ai-employee.png");

function parseQuery(query) {
  const out = {};
  String(query || "").split("&").forEach((part) => {
    if (!part) return;
    const pair = part.split("=");
    const key = decodeURIComponent(pair[0] || "").trim();
    if (!key) return;
    out[key] = decodeURIComponent(pair.slice(1).join("=") || "");
  });
  return out;
}

function withInvitePath(path) {
  const app = getApp();
  if (app && typeof app.sharePath === "function") return app.sharePath(path || "/pages/index/index");
  return path || "/pages/index/index";
}

function withInviteQuery(query) {
  const app = getApp();
  if (app && typeof app.shareQuery === "function") return app.shareQuery(parseQuery(query));
  return query || "";
}

function appShare(options) {
  const opts = options || {};
  return {
    title: opts.title || "必火AI员工 - 数字人和AI视频创作",
    path: withInvitePath(opts.path),
    imageUrl: opts.imageUrl || DEFAULT_IMAGE
  };
}

function timelineShare(options) {
  const opts = options || {};
  return {
    title: opts.title || DEFAULT_TITLE,
    query: withInviteQuery(opts.query),
    imageUrl: opts.imageUrl || DEFAULT_IMAGE
  };
}

function showShareMenu() {
  if (!wx.showShareMenu) return;
  wx.showShareMenu({
    withShareTicket: true,
    menus: ["shareAppMessage", "shareTimeline"]
  });
}

module.exports = {
  appShare,
  timelineShare,
  showShareMenu,
  DEFAULT_IMAGE
};
