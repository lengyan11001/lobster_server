const api = require("./api");

function mediaExt(item) {
  const title = item.title || item.asset_id || "lobster-media";
  const match = String(title).match(/\.[A-Za-z0-9]{2,5}$/);
  if (match) return match[0].toLowerCase();
  if (item.media_type === "video") return ".mp4";
  if (item.media_type === "image") return ".jpg";
  if (item.media_type === "audio") return ".mp3";
  return ".dat";
}

function safeName(item) {
  const base = item.asset_id || item.id || `media_${Date.now()}`;
  return String(base).replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 80) + mediaExt(item);
}

function copyLink(url) {
  return new Promise((resolve, reject) => {
    wx.setClipboardData({
      data: url,
      success: resolve,
      fail: reject
    });
  });
}

function downloadToFile(url, filePath) {
  if (!url) return Promise.reject(new Error("没有可下载链接"));
  return new Promise((resolve, reject) => {
    wx.showLoading({ title: "下载中", mask: true });
    wx.downloadFile({
      url,
      filePath,
      timeout: 120000,
      success(res) {
        const localPath = res.filePath || res.tempFilePath;
        if (!localPath || Number(res.statusCode || 0) >= 400) {
          reject(new Error(`下载失败 ${res.statusCode || ""}`));
          return;
        }
        resolve(localPath);
      },
      fail(err) {
        reject(new Error(api.errorMessage(err)));
      },
      complete() {
        wx.hideLoading();
      }
    });
  });
}

function albumSave(item, localPath) {
  return new Promise((resolve, reject) => {
    const onFail = (err) => reject(new Error(api.errorMessage(err)));
    if (item.media_type === "image") {
      wx.saveImageToPhotosAlbum({ filePath: localPath, success: () => resolve(localPath), fail: onFail });
    } else if (item.media_type === "video") {
      wx.saveVideoToPhotosAlbum({ filePath: localPath, success: () => resolve(localPath), fail: onFail });
    } else {
      copyLink(item.url || item.download_url || "").then(() => resolve(localPath)).catch(onFail);
    }
  });
}

function saveToAlbum(item) {
  const directUrl = item.download_url || item.preview_url || item.url || "";
  const fallbackUrl = item.proxy_download_url || item.proxy_preview_url || "";
  if (!directUrl && !fallbackUrl) return Promise.reject(new Error("没有可下载链接"));
  const filePath = `${wx.env.USER_DATA_PATH}/${safeName(item)}`;
  return downloadToFile(directUrl, filePath)
    .catch((err) => {
      if (!fallbackUrl || fallbackUrl === directUrl) throw err;
      return downloadToFile(fallbackUrl, filePath);
    })
    .then((localPath) => albumSave(item, localPath));
}

module.exports = {
  copyLink,
  saveToAlbum,
  safeName
};
