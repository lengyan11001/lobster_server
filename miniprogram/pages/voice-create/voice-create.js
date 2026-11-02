const app = getApp();
const api = require("../../utils/api");

const LANGS = ["中文", "英文"];
const LANG_VALUES = ["zh", "en"];

Page({
  data: {
    title: "",
    languages: LANGS,
    langIndex: 0,
    filePath: "",
    fileName: "",
    agree: false,
    submitting: false,
    statusText: ""
  },

  onTitle(e) { this.setData({ title: e.detail.value }); },
  pickLang(e) { this.setData({ langIndex: Number(e.detail.value || 0) }); },
  toggleAgree(e) { this.setData({ agree: Boolean(e.detail.value) }); },

  chooseFile() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ["mp3", "wav", "m4a", "aac"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.setData({ filePath: file.path, fileName: String(file.name || "已选音频") });
      }
    });
  },

  clearFile() { this.setData({ filePath: "", fileName: "" }); },

  submit() {
    const d = this.data;
    if (!String(d.title || "").trim()) return wx.showToast({ title: "填写名称", icon: "none" });
    if (!d.filePath) return wx.showToast({ title: "选择声音文件", icon: "none" });
    if (!d.agree) return wx.showToast({ title: "请先勾选授权确认", icon: "none" });

    this.setData({ submitting: true, statusText: "提交克隆任务…" });
    api.uploadFile({
      url: "/api/hifly/my/voice/create-upload",
      filePath: d.filePath,
      name: "file",
      formData: {
        title: String(d.title).trim(),
        languages: LANG_VALUES[d.langIndex] || "zh",
        voice_type: "8"
      },
      token: app.globalData.token || "",
      timeout: 300000
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