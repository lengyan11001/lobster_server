const staticAssets = require("./static_assets");

const DEFAULT_TITLE = "必火AI员工";
const DEFAULT_IMAGE = staticAssets.staticAssetUrl("openclaw-hero-bg.jpg");

function appShare(options) {
  const opts = options || {};
  return {
    title: opts.title || "必火AI员工 - 数字人和AI视频创作",
    path: opts.path || "/pages/index/index",
    imageUrl: opts.imageUrl || DEFAULT_IMAGE
  };
}

function timelineShare(options) {
  const opts = options || {};
  return {
    title: opts.title || DEFAULT_TITLE,
    query: opts.query || "",
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
