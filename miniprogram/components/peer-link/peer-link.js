const api = require("../../utils/api");
const media = require("../../utils/media");
const { createPoller } = require("../../utils/task-poll");

Component({
  data: {
    open: false,
    link: "",
    text: "",
    videoUrl: "",
    busy: false,
    progressText: "",
    script: "",
    titleDraft: "",
    scriptBusy: false
  },

  methods: {
    open() {
      this.setData({ open: true, link: "", text: "", videoUrl: "", busy: false, progressText: "", script: "", titleDraft: "", scriptBusy: false });
    },

    close() {
      if (this.poller) { this.poller.stop(); this.poller = null; }
      this.setData({ open: false, busy: false, scriptBusy: false, progressText: "" });
    },

    noop() {},

    onLink(e) { this.setData({ link: e.detail.value || "" }); },

    extractText() { this.startJob("transcript"); },

    downloadVideo() { this.startJob("video"); },

    startJob(mode) {
      const app = getApp();
      const link = String(this.data.link || "").trim();
      if (!link) return wx.showToast({ title: "先粘贴同行链接", icon: "none" });
      if (!app.globalData.token) return wx.showToast({ title: "请先登录", icon: "none" });
      const isVideo = mode === "video";
      this.setData({ busy: true, text: "", videoUrl: "", script: "", titleDraft: "", progressText: isVideo ? "正在准备视频…" : "正在解析链接…" });
      app.request({ method: "POST", url: "/api/digital-human/peer-jobs", data: { url: link, mode }, timeout: 30000 })
        .then((data) => {
          const jobId = String((data && data.job_id) || "");
          if (!jobId) throw new Error("没有返回任务号");
          this.setData({ progressText: isVideo ? "正在准备视频…" : "正在下载音频并转写…" });
          this.startPolling(jobId, mode);
        })
        .catch((err) => {
          this.setData({ busy: false, progressText: "" });
          wx.showToast({ title: api.errorMessage(err) || "处理失败", icon: "none" });
        });
    },

    startPolling(jobId, mode) {
      const app = getApp();
      const isVideo = mode === "video";
      if (this.poller) this.poller.stop();
      this.poller = createPoller(
        () => app.request({ url: "/api/digital-human/peer-jobs/" + encodeURIComponent(jobId), timeout: 30000 }),
        (data) => {
          const status = String((data && data.status) || "");
          if (status === "completed") {
            if (isVideo) {
              const url = String((data && data.video_url) || "");
              this.setData({ videoUrl: url, busy: false, progressText: "" });
              if (url) this.saveToAlbum(); else wx.showToast({ title: "视频没准备好", icon: "none" });
            } else {
              this.setData({ text: String((data && data.transcript) || ""), busy: false, progressText: "" });
              wx.showToast({ title: "转写完成", icon: "success" });
            }
            return true;
          }
          if (status === "failed") {
            this.setData({ busy: false, progressText: "" });
            wx.showToast({ title: String((data && data.error) || "处理失败").slice(0, 40), icon: "none" });
            return true;
          }
          const stage = String((data && data.stage) || "");
          this.setData({ progressText: isVideo ? "正在准备视频…" : (stage === "transcribing" ? "正在转写音频…" : "正在解析链接…") });
          return false;
        },
        () => {}
      );
      this.poller.start(true);
    },

    saveToAlbum() {
      const url = String(this.data.videoUrl || "").trim();
      if (!url) return wx.showToast({ title: "没有可保存的视频", icon: "none" });
      media.saveToAlbum({ url, media_type: "video", title: "同行视频" })
        .then(() => wx.showToast({ title: "已保存到相册", icon: "success" }))
        .catch((err) => wx.showToast({ title: api.errorMessage(err) || "保存失败", icon: "none" }));
    },

    copyText() {
      const text = String(this.data.text || "").trim();
      if (!text) return;
      wx.setClipboardData({ data: text, success: () => wx.showToast({ title: "已复制", icon: "success" }) });
    },

    writeScript() {
      const app = getApp();
      const transcript = String(this.data.text || "").trim();
      if (!transcript || this.data.scriptBusy) return;
      this.setData({ scriptBusy: true });
      app.request({ method: "POST", url: "/api/digital-human/peer-script", data: { transcript }, timeout: 180000 })
        .then((data) => {
          this.setData({
            titleDraft: String((data && data.title) || ""),
            script: String((data && data.script) || "")
          });
        })
        .catch((err) => wx.showToast({ title: api.errorMessage(err) || "生成失败", icon: "none" }))
        .then(() => this.setData({ scriptBusy: false }));
    },

    applyScript() {
      const script = String(this.data.script || "").trim();
      if (!script) return;
      const title = String(this.data.titleDraft || "").trim();
      this.triggerEvent("apply", { title, script });
      this.close();
    }
  }
});
