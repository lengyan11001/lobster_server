const config = require("../config");

function buildUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${config.API_BASE.replace(/\/+$/, "")}/${String(path || "").replace(/^\/+/, "")}`;
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
      "content-type": "application/json"
    },
    options.header || {}
  );
  if (token) header.Authorization = `Bearer ${token}`;

  return new Promise((resolve, reject) => {
    wx.request({
      url: buildUrl(options.url),
      method,
      data: options.data || {},
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
  const header = Object.assign({}, options.header || {});
  if (token) header.Authorization = `Bearer ${token}`;

  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: buildUrl(options.url),
      filePath: options.filePath,
      name: options.name || "file",
      formData: options.formData || {},
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
