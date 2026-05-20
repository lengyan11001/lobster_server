const api = require("./api");

function assetUrl(path) {
  const value = String(path || "").trim();
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  if (/^\/\//.test(value)) return `https:${value}`;
  return api.buildUrl(value);
}

function baseAvatarTitle(title) {
  const raw = String(title || "").trim();
  const value = raw
    .replace(/[（(][^）)]*(视频素材|素材|分享|直播|横版|竖版|模板)[^）)]*[）)]/g, "")
    .replace(/[-_—｜|]?(视频素材|数字人|公共数字人|分享|直播|素材|模板|横版|竖版)\d*$/g, "")
    .replace(/[-_—｜|]?\d+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return value || raw;
}

function normalizePublicAvatarTemplate(row) {
  const avatar = String((row && row.avatar) || "").trim();
  if (!avatar) return null;
  const title = String((row && (row.title || row.name || row.avatar)) || avatar).trim();
  const rawImage = row && (row.image_url || row.cover_url || row.detail_url || row.thumb || row.avatar_url || row.poster_url || row.thumbnail_url || row.preview_url);
  const imageUrl = assetUrl(rawImage);
  return {
    avatar,
    id: `public:${avatar}`,
    title,
    base_title: baseAvatarTitle(title),
    image_url: imageUrl,
    cover_url: imageUrl,
    section: "public",
    section_label: "公共数字人",
    status: (row && row.status) || "success",
    raw: row || {}
  };
}

function pickPublicAvatarTemplates(rows, limit) {
  const out = [];
  const seenAvatar = {};
  const seenTitle = {};
  const normalized = (rows || []).map(normalizePublicAvatarTemplate).filter(Boolean);
  normalized.forEach((item) => {
    if (!item.image_url) return;
    if (seenAvatar[item.avatar] || seenTitle[item.base_title]) return;
    seenAvatar[item.avatar] = true;
    seenTitle[item.base_title] = true;
    out.push(item);
  });
  if (out.length < limit) {
    normalized.forEach((item) => {
      if (seenAvatar[item.avatar]) return;
      seenAvatar[item.avatar] = true;
      out.push(item);
    });
  }
  return out.slice(0, limit || 20);
}

function storeDigitalAvatarPrefill(item) {
  if (!item || !item.avatar) return false;
  wx.setStorageSync("lobster_digital_prefill", {
    pageMode: "create",
    avatar: {
      avatar: item.avatar,
      id: item.id || `public:${item.avatar}`,
      title: item.title || item.avatar,
      image_url: item.image_url || item.cover_url || "",
      cover_url: item.cover_url || item.image_url || "",
      section: item.section || "public",
      section_label: item.section_label || "公共数字人",
      status: item.status || "success"
    }
  });
  return true;
}

module.exports = {
  assetUrl,
  baseAvatarTitle,
  normalizePublicAvatarTemplate,
  pickPublicAvatarTemplates,
  storeDigitalAvatarPrefill
};
