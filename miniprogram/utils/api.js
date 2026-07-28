const config = require("../config");

function buildUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  const joined = `${config.API_BASE.replace(/\/+$/, "")}/${String(path || "").replace(/^\/+/, "")}`;
  const separator = joined.indexOf("?") >= 0 ? "&" : "?";
  return `${joined}${separator}brand=${encodeURIComponent(config.BRAND_MARK)}`;
}

function errorMessage(err) {
  if (!err) return "请求失败";
  if (typeof err === "string") return err;
  if (err.detail) return typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
  if (err.errMsg) return err.errMsg;
  if (err.message) return err.message;
  return "请求失败";
}

function request(options) {
  const method = options.method || "GET";
  const token = options.token || "";
  const header = Object.assign(
    {
      "content-type": "application/json",
      "X-Lobster-Brand": config.BRAND_MARK
    },
    options.header || {}
  );
  if (token) header.Authorization = `Bearer ${token}`;

  return new Promise((resolve, reject) => {
    wx.request({
      url: buildUrl(options.url),
      method,
      data: Object.assign({}, options.data || {}, { brand_mark: config.BRAND_MARK }),
      header,
      timeout: options.timeout || 20000,
      success(res) {
        const status = Number(res.statusCode || 0);
        if (status >= 200 && status < 300) {
          resolve(res.data || {});
          return;
        }
        reject(new Error(errorMessage(res.data) || `请求失败 ${status}`));
      },
      fail(err) {
        reject(new Error(errorMessage(err)));
      }
    });
  });
}

function uploadFile(options) {
  const token = options.token || "";
  const header = Object.assign({ "X-Lobster-Brand": config.BRAND_MARK }, options.header || {});
  if (token) header.Authorization = `Bearer ${token}`;

  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: buildUrl(options.url),
      filePath: options.filePath,
      name: options.name || "file",
      formData: Object.assign({}, options.formData || {}, { brand_mark: config.BRAND_MARK }),
      header,
      timeout: options.timeout || 120000,
      success(res) {
        const status = Number(res.statusCode || 0);
        let data = {};
        try {
          data = typeof res.data === "string" ? JSON.parse(res.data || "{}") : res.data || {};
        } catch (err) {
          data = { detail: res.data || "" };
        }
        if (status >= 200 && status < 300) {
          resolve(data || {});
          return;
        }
        reject(new Error(errorMessage(data) || `上传失败 ${status}`));
      },
      fail(err) {
        reject(new Error(errorMessage(err)));
      }
    });
  });
}

module.exports = {
  request,
  uploadFile,
  buildUrl,
  errorMessage
};
