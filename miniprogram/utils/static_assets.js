const STATIC_ASSET_BASE = "https://aihuoke-1409040632.cos.ap-shanghai.myqcloud.com/client/miniprogram";

function staticAssetUrl(path) {
  const clean = String(path || "").replace(/^\/+/, "");
  return clean ? `${STATIC_ASSET_BASE}/${clean}` : STATIC_ASSET_BASE;
}

function hiflyAvatarUrl(filename) {
  const clean = String(filename || "").replace(/^\/+/, "");
  return clean ? staticAssetUrl(`hifly_avatars/${clean}`) : "";
}

module.exports = {
  STATIC_ASSET_BASE,
  staticAssetUrl,
  hiflyAvatarUrl
};
