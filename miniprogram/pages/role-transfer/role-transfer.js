const app = getApp();
const api = require("../../utils/api");
const share = require("../../utils/share");
const staticAssets = require("../../utils/static_assets");

const MAX_VIDEO_SECONDS = 30;
const CREDITS_PER_SECOND = 90;

const SAMPLE_IMAGES = [
  {
    title: "JK美女",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/guzhuang-beauty.png"
  },
  {
    title: "西装帅哥",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/suit-man.png"
  },
  {
    title: "古装美女",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/jk-girl.png"
  }
];

const SAMPLE_VIDEOS = [
  {
    title: "dadada舞蹈",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-dadada-1.mp4",
    cover_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-dadada-1.jpg",
    duration: 17.854
  },
  {
    title: "美式舞蹈",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-american.mp4",
    cover_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-american.jpg",
    duration: 13.934
  },
  {
    title: "游山恋舞蹈",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-youshanlian.mp4",
    cover_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-youshanlian.jpg",
    duration: 15.867
  },
  {
    title: "基础爵士",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-jazz-basic.mp4",
    cover_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-jazz-basic.jpg",
    duration: 19.067
  },
  {
    title: "灰墙舞蹈",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-dadada-2.mp4",
    cover_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-dadada-2.jpg",
    duration: 10.334
  },
  {
    title: "C哩C哩舞",
    source_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-cilili.mp4",
    cover_url: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/videos/dance-cilili.jpg",
    duration: 12.949
  }
];

function billableSeconds(value) {
  const seconds = Number(value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return MAX_VIDEO_SECONDS;
  return Math.ceil(seconds);
}

function estimateCredits(value) {
  const seconds = billableSeconds(value);
  return seconds > 0 ? seconds * CREDITS_PER_SECOND : 0;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 19);
  const pad = (num) => String(num).padStart(2, "0");
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function filenameFromUrl(url) {
  const path = String(url || "").split("?")[0].split("#")[0];
  const name = decodeURIComponent(path.split("/").pop() || "");
  return name || "wan-role-video.mp4";
}

function normalizeTask(item) {
  const url = item.playable_url || item.video_result_url || item.asset_video_url || item.source_video_url || "";
  return Object.assign({}, item, {
    playable_url: url,
    created_at_text: formatTime(item.created_at),
    title: item.title || (item.task_type === "mix" ? "角色替换" : "动作迁移"),
    status_label: item.status_label || (item.status === "success" ? "已完成" : item.status === "failed" ? "失败" : "生成中"),
    estimate_text: item.status === "processing" ? "预计5-10分钟" : "",
    filename: filenameFromUrl(url)
  });
}

Page({
  data: {
    heroBgUrl: staticAssets.staticAssetUrl("openclaw-hero-bg.jpg"),
    moveBgUrl: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/jk-girl.png",
    mixBgUrl: "https://cdn-video.51sux.com/assets/miniprogram/role-transfer/guzhuang-beauty.png",
    phoneBound: false,
    authPanelVisible: false,
    taskType: "move",
    imageAsset: null,
    videoAsset: null,
    sampleImages: SAMPLE_IMAGES,
    sampleVideos: SAMPLE_VIDEOS,
    sampleImageIndex: -1,
    sampleVideoIndex: -1,
    maxVideoSeconds: MAX_VIDEO_SECONDS,
    creditsPerSecond: CREDITS_PER_SECOND,
    estimatedCredits: 0,
    billableSeconds: 0,
    costHint: `每秒消耗${CREDITS_PER_SECOND}算力`,
    uploadingImage: false,
    uploadingVideo: false,
    submitting: false,
    previewVisible: false,
    previewVideoUrl: "",
    previewVideoTitle: ""
  },

  pollTimer: null,

  onLoad() {
    share.showShareMenu();
    this.refreshAuthState();
  },

  onShow() {
    this.refreshAuthState();
  },

  onHide() {
    this.stopPolling();
  },

  onUnload() {
    this.stopPolling();
  },

  onPullDownRefresh() {
    wx.stopPullDownRefresh();
  },

  refreshAuthState() {
    app.restoreSession();
    const phoneBound = Boolean(app.globalData.token && app.globalData.phone);
    this.setData({ phoneBound });
    return phoneBound;
  },

  showAuthPanel() {
    if (this.refreshAuthState()) return false;
    this.setData({ authPanelVisible: true });
    return true;
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then(() => {
        this.refreshAuthState();
        if (app.globalData.phone) {
          this.setData({ authPanelVisible: false });
        } else {
          wx.showToast({ title: "请绑定手机号", icon: "none" });
        }
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      wx.showToast({ title: "微信取号失败，请到我的页面绑定", icon: "none" });
      return;
    }
    const bind = () => {
      app
        .bindPhone(code)
        .then(() => {
          this.refreshAuthState();
          this.setData({ authPanelVisible: false });
          wx.showToast({ title: "登录成功", icon: "success" });
        })
        .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
    };
    if (!app.globalData.token) {
      app.loginWithWechat().then(bind).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    bind();
  },

  goBack() {
    if (getCurrentPages().length > 1) wx.navigateBack();
    else wx.switchTab({ url: "/pages/index/index" });
  },

  setTaskType(evt) {
    this.setData({ taskType: evt.currentTarget.dataset.type || "move" });
  },

  chooseImage() {
    if (this.showAuthPanel()) return;
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (file && file.tempFilePath) this.uploadPickedFile(file.tempFilePath, "image");
      }
    });
  },

  selectSampleImage(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.sampleImages[index];
    if (!item || !item.source_url) return;
    this.setData({
      sampleImageIndex: index,
      imageAsset: {
        asset_id: "",
        source_url: item.source_url,
        media_type: "image",
        local_path: item.source_url,
        title: item.title
      }
    });
  },

  selectSampleVideo(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.sampleVideos[index];
    if (!item || !item.source_url) return;
    const seconds = billableSeconds(item.duration);
    const credits = estimateCredits(item.duration);
    this.setData({
      sampleVideoIndex: index,
      billableSeconds: seconds,
      estimatedCredits: credits,
      costHint: `预计消耗${credits}算力（${seconds}秒）`,
      videoAsset: {
        asset_id: "",
        source_url: item.source_url,
        cover_url: item.cover_url,
        media_type: "video",
        local_path: item.source_url,
        title: item.title,
        duration: item.duration
      }
    });
  },

  previewSampleVideo(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.sampleVideos[index];
    if (!item || !item.source_url) return;
    this.setData({
      previewVisible: true,
      previewVideoUrl: item.source_url,
      previewVideoTitle: item.title || "视频预览"
    });
  },

  closeVideoPreview() {
    this.setData({
      previewVisible: false,
      previewVideoUrl: "",
      previewVideoTitle: ""
    });
  },

  noop() {},

  chooseVideo() {
    if (this.showAuthPanel()) return;
    wx.chooseMedia({
      count: 1,
      mediaType: ["video"],
      sourceType: ["album", "camera"],
      maxDuration: 30,
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        const duration = Number(file && file.duration ? file.duration : 0);
        if (duration && duration > MAX_VIDEO_SECONDS) {
          wx.showToast({ title: `视频最长${MAX_VIDEO_SECONDS}秒，请裁剪后上传`, icon: "none" });
          return;
        }
        if (file && file.tempFilePath) this.uploadPickedFile(file.tempFilePath, "video", duration);
      }
    });
  },

  uploadPickedFile(filePath, kind, duration = 0) {
    const key = kind === "image" ? "uploadingImage" : "uploadingVideo";
    this.setData({ [key]: true });
    api
      .uploadFile({
        url: "/api/wan/role-transfer/upload",
        filePath,
        name: "file",
        formData: { media_type: kind },
        token: app.globalData.token || wx.getStorageSync("lobster_token") || "",
        timeout: 180000
      })
      .then((data) => {
        const fileDuration = Number(data.duration || duration || 0);
        const seconds = kind === "video" ? billableSeconds(fileDuration) : this.data.billableSeconds;
        const credits = kind === "video" ? estimateCredits(fileDuration) : this.data.estimatedCredits;
        const asset = {
          asset_id: data.asset_id,
          source_url: data.source_url || data.url,
          media_type: data.media_type,
          local_path: filePath,
          duration: fileDuration
        };
        if (kind === "image") this.setData({ imageAsset: asset, sampleImageIndex: -1 });
        else {
          this.setData({
            videoAsset: asset,
            sampleVideoIndex: -1,
            billableSeconds: seconds,
            estimatedCredits: credits,
            costHint: seconds > 0 ? `预计消耗${credits}算力（${seconds}秒）` : `每秒消耗${CREDITS_PER_SECOND}算力`
          });
        }
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ [key]: false }));
  },

  submitTask() {
    if (this.showAuthPanel()) return;
    const image = this.data.imageAsset;
    const video = this.data.videoAsset;
    if (!image || !image.source_url) {
      wx.showToast({ title: "请先添加图片", icon: "none" });
      return;
    }
    if (!video || !video.source_url) {
      wx.showToast({ title: "请先添加视频", icon: "none" });
      return;
    }
    const title = this.data.taskType === "mix" ? "角色替换" : "动作迁移";
    this.setData({ submitting: true });
    app
      .request({
        method: "POST",
        url: "/api/wan/role-transfer/tasks",
        data: {
          task_type: this.data.taskType,
          image_url: image.source_url,
          video_url: video.source_url,
          video_duration: video.duration || this.data.billableSeconds || MAX_VIDEO_SECONDS,
          mode: "wan-std",
          title
        },
        timeout: 180000
      })
      .then((data) => {
        this.setData({
          imageAsset: null,
          videoAsset: null,
          sampleImageIndex: -1,
          sampleVideoIndex: -1,
          billableSeconds: 0,
          estimatedCredits: 0,
          costHint: `每秒消耗${CREDITS_PER_SECOND}算力`
        });
        wx.showModal({
          title: "任务已创建",
          content: "任务已提交，预计5-10分钟生成结果。可到作品页的超级视频查看进度。",
          confirmText: "去作品页",
          cancelText: "继续创作",
          success: (res) => {
            if (res.confirm) {
              wx.setStorageSync("lobster_open_super_video", true);
              wx.switchTab({ url: "/pages/downloads/downloads" });
            }
          }
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ submitting: false }));
  },

  startPollingIfNeeded() {},

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  onShareAppMessage() {
    return share.appShare({
      title: "AI角色替换·动作迁移",
      path: "/pages/role-transfer/role-transfer",
      imageUrl: this.data.heroBgUrl
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "AI角色替换·动作迁移",
      imageUrl: this.data.heroBgUrl
    });
  }
});
