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

function saveToAlbum(item) {
  const url = item.download_url || item.preview_url || item.url || "";
  if (!url) return Promise.reject(new Error("没有可下载链接"));
  const filePath = `${wx.env.USER_DATA_PATH}/${safeName(item)}`;
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
        const onSuccess = () => resolve(localPath);
        const onFail = (err) => reject(new Error(api.errorMessage(err)));
        if (item.media_type === "image") {
          wx.saveImageToPhotosAlbum({ filePath: localPath, success: onSuccess, fail: onFail });
        } else if (item.media_type === "video") {
          wx.saveVideoToPhotosAlbum({ filePath: localPath, success: onSuccess, fail: onFail });
        } else {
          copyLink(item.url || url).then(() => resolve(localPath)).catch(onFail);
        }
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

module.exports = {
  copyLink,
  saveToAlbum,
  safeName
};

