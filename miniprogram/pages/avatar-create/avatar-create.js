const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");

function brandShortName() {
  return String(config.APP_NAME || config.BRAND_MARK || "").replace(/\s*(AI员工|AI智能体|AI助手)\s*$/i, "").trim();
}

function defaultTrainingText() {
  return "大家好，我正在录制数字人训练素材。接下来我会保持自然的表情和语速，清晰完整地朗读这段内容。希望这个数字人能够真实呈现我的形象和声音，帮助我更高效地进行内容创作。感谢大家的关注与支持。";
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
    trainingText: defaultTrainingText(),
    authVideoPath: "",
    authVideoName: "",
    authText: defaultAuthText(),
    agree: false,
    submitting: false,
    statusText: "",
    captureOpen: false,
    captureMode: "photo",
    captureTarget: "source",
    cameraPos: "front",
    recording: false,
    recordSeconds: 0,
    capturedPath: "",
    capturedKind: "",
    captureStatus: "",
    captureScript: "",
    recordTimeText: "00:00"
  },

  onLoad() {},

  onTitle(e) { this.setData({ title: e.detail.value }); },
  setSourceType(e) { this.setData({ sourceType: String(e.currentTarget.dataset.t || "image"), sourcePath: "", sourceName: "" }); },
  onTrainingText(e) { this.setData({ trainingText: e.detail.value }); },
  fillTrainingSample() { this.setData({ trainingText: defaultTrainingText() }); },
  copyAuthText() {
    wx.setClipboardData({
      data: String(this.data.authText || ""),
      success: () => wx.showToast({ title: "授权文案已复制", icon: "success" })
    });
  },

  toggleAgree(e) { const values = (e.detail && e.detail.value) || []; this.setData({ agree: values.length > 0 }); },

  openCapture(e) {
    const mode = String(e.currentTarget.dataset.mode || "photo");
    const target = String(e.currentTarget.dataset.target || "source");
    const script = mode !== "video"
      ? ""
      : (target === "auth" ? String(this.data.authText || "").trim() : String(this.data.trainingText || "").trim());
    this.setData({
      captureOpen: true, captureMode: mode, captureTarget: target,
      recording: false, recordSeconds: 0, recordTimeText: "00:00", capturedPath: "", capturedKind: "",
      cameraPos: "front", captureScript: script,
      captureStatus: "正在打开摄像头…"
    });
    setTimeout(() => {
      this._camera = wx.createCameraContext();
      this.setData({ captureStatus: mode === "video" ? "按下圆形按钮开始录像" : "调整画面后按下圆形按钮拍照" });
    }, 400);
  },

  onCameraError() {
    this.setData({ captureStatus: "无法打开摄像头，请在系统设置里允许微信使用相机" });
  },

  switchCaptureMode(e) {
    if (this.data.recording) return;
    const mode = String(e.currentTarget.dataset.mode || "photo");
    const script = mode !== "video"
      ? ""
      : (this.data.captureTarget === "auth" ? String(this.data.authText || "").trim() : String(this.data.trainingText || "").trim());
    this.setData({ captureMode: mode, captureScript: script, capturedPath: "", capturedKind: "", captureStatus: mode === "video" ? "按下圆形按钮开始录像" : "调整画面后按下圆形按钮拍照" });
  },

  switchCamera() {
    this.setData({ cameraPos: this.data.cameraPos === "front" ? "back" : "front" });
  },

  closeCapture() {
    clearInterval(this._capTimer);
    if (this.data.recording) { try { this._camera && this._camera.stopRecord(); } catch (err) {} }
    this.setData({ captureOpen: false, recording: false, capturedPath: "", capturedKind: "" });
  },

  captureAction() {
    if (!this._camera) this._camera = wx.createCameraContext();
    if (this.data.capturedPath) return;
    if (this.data.captureMode === "photo") {
      this._camera.takePhoto({
        quality: "high",
        success: (res) => this.setData({ capturedPath: res.tempImagePath, capturedKind: "image", captureStatus: "照片已拍好，点「使用素材」确认" }),
        fail: () => wx.showToast({ title: "拍照失败", icon: "none" })
      });
      return;
    }
    if (this.data.recording) {
      this._camera.stopRecord({
        success: (res) => { clearInterval(this._capTimer); this.setData({ recording: false, capturedPath: res.tempVideoPath, capturedKind: "video", captureStatus: "视频已录好，点「使用素材」确认" }); },
        fail: () => { clearInterval(this._capTimer); this.setData({ recording: false }); }
      });
      return;
    }
    this.setData({ recording: true, recordSeconds: 0, recordTimeText: "00:00", captureStatus: "正在录像…再按一次停止" });
    this._camera.startRecord({ timeoutCallback: () => this.stopRecordingAuto() });
    this._capTimer = setInterval(() => {
      const sec = this.data.recordSeconds + 1;
      const mm = String(Math.floor(sec / 60)).padStart(2, "0");
      const ss = String(sec % 60).padStart(2, "0");
      this.setData({ recordSeconds: sec, recordTimeText: mm + ":" + ss });
    }, 1000);
  },

  stopRecordingAuto() {
    clearInterval(this._capTimer);
    this.setData({ recording: false, captureStatus: "已到 60 秒自动停止" });
  },

  retakeCapture() {
    this.setData({ capturedPath: "", capturedKind: "", recordSeconds: 0, recordTimeText: "00:00", captureStatus: this.data.captureMode === "video" ? "按下圆形按钮开始录像" : "调整画面后按下圆形按钮拍照" });
  },

  useCapture() {
    const path = this.data.capturedPath;
    const kind = this.data.capturedKind;
    if (!path) return;
    if (this.data.captureTarget === "auth") {
      this.setData({ authVideoPath: path, authVideoName: "已拍摄授权视频", captureOpen: false, capturedPath: "" });
    } else if (kind === "video") {
      this.setData({ sourceType: "video", sourcePath: path, sourceName: "已拍摄视频", captureOpen: false, capturedPath: "" });
    } else {
      this.setData({ sourceType: "image", sourcePath: path, sourceName: "已拍摄照片", captureOpen: false, capturedPath: "" });
    }
  },

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