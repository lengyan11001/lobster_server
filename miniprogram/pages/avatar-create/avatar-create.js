const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");

function brandShortName() {
  return String(config.APP_NAME || config.BRAND_MARK || "").replace(/\s*(AI员工|AI智能体|AI助手)\s*$/i, "").trim();
}

function defaultAuthText() {
  return `案例：我是xxx（真实姓名），我授权【${brandShortName()}】使用视频中的肖像、声音，为我生成定制数字人及声音，并在本人【${brandShortName()}】账号中创作使用。`;
}

Page({
  data: {
    title: "",
    sourceType: "image",
    sourcePath: "",
    sourceName: "",
    trainingText: "大家好，我正在录制数字人训练素材。接下来我会保持自然的表情和语速，清晰完整地朗读这段内容。",
    authVideoPath: "",
    authVideoName: "",
    authText: "",
    agree: false,
    submitting: false,
    statusText: ""
  },

  onLoad() {
    this.setData({ authText: defaultAuthText() });
  },

  onTitle(e) { this.setData({ title: e.detail.value }); },
  setSourceType(e) { this.setData({ sourceType: String(e.currentTarget.dataset.t || "image"), sourcePath: "", sourceName: "" }); },
  onTrainingText(e) { this.setData({ trainingText: e.detail.value }); },
  onAuthText(e) { this.setData({ authText: e.detail.value }); },
  toggleAgree(e) { this.setData({ agree: Boolean(e.detail.value) }); },

  chooseSource() {
    const type = this.data.sourceType;
    wx.chooseMedia({
      count: 1,
      mediaType: [type],
      maxDuration: 60,
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.setData({ sourcePath: file.tempFilePath, sourceName: type === "image" ? "已选图片" : "已选视频" });
      }
    });
  },

  clearSource() { this.setData({ sourcePath: "", sourceName: "" }); },

  chooseAuthVideo() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["video"],
      maxDuration: 60,
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.setData({ authVideoPath: file.tempFilePath, authVideoName: "已选授权视频" });
      }
    });
  },

  clearAuthVideo() { this.setData({ authVideoPath: "", authVideoName: "" }); },

  upload(filePath, mediaType) {
    return api.uploadFile({
      url: "/api/assets/upload",
      filePath,
      name: "file",
      formData: { media_type: mediaType, tags: "digital_human_v2,clone_source" },
      token: app.globalData.token || "",
      timeout: 300000
    }).then((data) => {
      const id = String((data && data.asset_id) || "").trim();
      if (!id) throw new Error("上传成功但没有返回素材 ID");
      return id;
    });
  },

  submit() {
    const d = this.data;
    if (!String(d.title || "").trim()) return wx.showToast({ title: "填写名称", icon: "none" });
    if (!d.sourcePath) return wx.showToast({ title: "选择训练素材", icon: "none" });
    if (!d.authVideoPath) return wx.showToast({ title: "请上传授权视频", icon: "none" });
    const auth = String(d.authText || "").trim();
    if (auth.length < 2) return wx.showToast({ title: "请填写授权说明", icon: "none" });
    if (auth.indexOf(brandShortName()) < 0) {
      return wx.showToast({ title: `授权说明必须包含「${brandShortName()}」`, icon: "none" });
    }
    if (!d.agree) return wx.showToast({ title: "请先勾选授权确认", icon: "none" });

    this.setData({ submitting: true, statusText: "1/3 上传训练素材" });
    const mediaType = d.sourceType === "image" ? "image" : "video";
    this.upload(d.sourcePath, mediaType)
      .then((sourceId) => {
        this.setData({ statusText: "2/3 上传授权视频" });
        return this.upload(d.authVideoPath, "video").then((authId) => {
          this.setData({ statusText: "3/3 创建数字人 2.0 训练任务" });
          return app.request({
            method: "POST",
            url: "/api/shanjian-digital-human/profile/train",
            data: {
              title: d.title.trim(),
              mode: d.sourceType === "video" ? "fast_video" : "image",
              image_asset_id: d.sourceType === "image" ? sourceId : "",
              video_asset_id: d.sourceType === "video" ? sourceId : "",
              auth_video_asset_id: authId,
              auth_text: String(d.authText).trim(),
              brand_mark: config.BRAND_MARK,
              make_default: true
            },
            timeout: 120000
          });
        });
      })
      .then(() => {
        wx.showToast({ title: "克隆任务已提交", icon: "success" });
        setTimeout(() => wx.navigateBack(), 800);
      })
      .catch((err) => {
        this.setData({ statusText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ submitting: false }));
  }
});