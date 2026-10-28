const app = getApp();
const api = require("../../utils/api");
const share = require("../../utils/share");
const config = require("../../config");

const POLL_MS = 6000;
const MAX_POLLS = 80;
const TRAIN_POLL_MS = 8000;
const TRAIN_MAX_POLLS = 120;
const SERVER_BASE = "https://bhzn.top";
const READY_STATUS = ["success", "succeed", "ready", "completed", "done"];
const EMOTION_LABELS = ["自然积极", "平静", "悲伤低落", "愤怒强烈"];
const EMOTION_VALUES = ["happy", "neutral", "sad", "angry"];
const MATCH_WAY_NAMES = ["模糊匹配", "精确匹配"];
const MATCH_WAY_VALUES = ["fuzzyMatch", "preciseMatch"];
const CLONE_LANGS = ["中文", "英文"];
const CLONE_LANG_VALUES = ["zh", "en"];

function assetUrl(value) {
  const u = String(value || "").trim();
  if (!u) return "";
  if (/^https?:\/\//i.test(u)) return u;
  if (u.charAt(0) === "/") return SERVER_BASE + u;
  return u;
}


function mediaBase() {
  return String(config.API_BASE || "").replace(/\/+$/, "");
}

function thumbUrl(url) {
  const u = String(url || "").trim();
  if (!u) return "";
  return `${mediaBase()}/api/media/thumb?url=${encodeURIComponent(u)}`;
}

function playUrl(url) {
  const u = String(url || "").trim();
  if (!u) return "";
  return `${mediaBase()}/api/media/proxy?url=${encodeURIComponent(u)}`;
}

function isPreviewVoice(voice) {
  return String(voice || "").indexOf("consumer_") === 0;
}

function normalizeProfiles(items) {
  return (items || []).map((row) => {
    const id = Number(row && row.id);
    if (!id) return null;
    const status = String((row && row.status) || "").toLowerCase();
    return {
      id,
      title: String(row.title || `数字人 ${id}`),
      initial: String(row.title || "人").trim().slice(0, 1) || "人",
      status_text: String(row.status_text || status || "未知"),
      ready: READY_STATUS.indexOf(status) >= 0,
      is_default: Boolean(row.is_default),
      cover_url: assetUrl(row.cover_url || ""),
      source_url: assetUrl(row.source_url || ""),
      kind: String(row.cover_url || "").trim() ? "image" : (/\.mp4|\.mov|\.webm/i.test(String(row.source_url || "")) ? "video" : "image"),
      thumb: String(row.cover_url || "").trim() ? assetUrl(row.cover_url) : thumbUrl(assetUrl(row.source_url))
    };
  }).filter(Boolean);
}

function normalizeVoices(items) {
  const out = [];
  (items || []).forEach((row) => {
    const styles = Array.isArray(row && row.styles) && row.styles.length ? row.styles : [row || {}];
    const baseTitle = String((row && (row.title || row.name || row.voice)) || "");
    const params = (row && row.voice_params) || {};
    const fallback = styles.find((s) => s && s.voice && !isPreviewVoice(s.voice)) || styles[0] || {};
    styles.forEach((style) => {
      const voice = String((style && style.voice) || (fallback && fallback.voice) || (row && row.voice) || "").trim();
      if (!voice || isPreviewVoice(voice)) return;
      const label = style && style.label && style.label !== "默认风格" ? `${baseTitle} - ${style.label}` : (baseTitle || voice);
      out.push({
        voice,
        title: label,
        provider: String((row && row.provider) || ""),
        rate: (style && style.rate) || params.rate || (row && row.rate) || "",
        volume: (style && style.volume) || params.volume || (row && row.volume) || "",
        pitch: (style && style.pitch) || params.pitch || (row && row.pitch) || "",
        emotion: (style && style.emotion) || params.emotion || (row && row.emotion) || ""
      });
    });
  });
  return out;
}

function findAudioUrl(input) {
  const keys = ["audio_url", "audioUrl", "url", "source_url", "file_url", "play_url"];
  const queue = [input];
  let guard = 0;
  while (queue.length && guard < 200) {
    guard += 1;
    const current = queue.shift();
    if (!current) continue;
    if (typeof current === "string") {
      if (/^https?:\/\//i.test(current) || /^data:audio\//i.test(current)) return current;
      continue;
    }
    if (typeof current !== "object") continue;
    for (let i = 0; i < keys.length; i += 1) {
      const value = current[keys[i]];
      if (typeof value === "string" && (/^https?:\/\//i.test(value) || /^data:audio\//i.test(value))) return value;
    }
    Object.keys(current).forEach((key) => {
      const value = current[key];
      if (value && typeof value === "object") queue.push(value);
    });
  }
  return "";
}
function dataUriToTempFile(dataUri) {
  const matched = /^data:audio\/[a-z0-9.+-]+;base64,(.+)$/i.exec(String(dataUri || ""));
  if (!matched) return "";
  try {
    const fs = wx.getFileSystemManager();
    const path = `${wx.env.USER_DATA_PATH}/tts_${Date.now()}.mp3`;
    fs.writeFileSync(path, matched[1], "base64");
    return path;
  } catch (err) {
    return "";
  }
}

Page({
  data: {
    phoneBound: false, maskedPhone: "", errText: "",
    profiles: [], profileIndex: -1, profilesLoading: false,
    voices: [], voiceNames: [], voiceIndex: -1, voicesLoading: false,
    trainOpen: false, trainMode: "image", trainFileName: "", trainFile: "",
    trainTitle: "", trainAuth: "", trainAgree: false, trainSubmitting: false, trainStatus: "",
    cloneOpen: false, cloneFileName: "", cloneFile: "", cloneTitle: "",
    cloneLanguages: CLONE_LANGS, cloneLangIndex: 0, cloneAgree: false,
    cloneSubmitting: false, cloneStatus: "",
    mode: "tts", title: "", taskName: "数字人口播", videoTitle: "", text: "", audioUrl: "", audioName: "",
    durationMode: "short", targetDuration: 60, templateMode: "none", advancedOpen: false,
    speed: 1, speedText: "1.00", volume: 1, volumeText: "1.00",
    pitch: 0, pitchText: "0", pitchMin: -12, pitchMax: 12, pitchStep: 1,
    emotions: EMOTION_LABELS, emotionIndex: 0,
    instructions: "",
    preprocessNames: ["粗剪", "切片拼接"], preprocessValues: ["roughCut", "sliceMerge"], preprocessIndex: 0,
    compositionNames: ["随机", "顺序"], compositionValues: ["random", "sequential"], compositionIndex: 0,
    useTemplate: false, templates: [], templateIndex: -1, templatesLoading: false,
    templateKeyword: "", templatePageSize: 20,
    headerSwitch: true, subtitleSwitch: true, keywordSwitch: true,
    materialSwitch: true, materialSoundSwitch: false, watermarkSwitch: false,
    matchWayNames: MATCH_WAY_NAMES, matchWayIndex: 0,
    materials: [], assetTab: "image", assets: [], assetsAll: [], assetsLoading: false,
    assetKeyword: "",
    submitting: false, statusText: "", videoUrl: "", coverUrl: "", history: [],
    pickerOpen: false, pickerItems: [], pickerLoading: false, extracting: false, extractStep: 0,
    dhTemplateName: "", dhTemplateDevice: "", dhTemplateStyleId: ""
  },

  onLoad() {
    this.pollTimer = null;
    this.pollCount = 0;
    this.trainTimer = null;
    this.cloneTimer = null;
    this.voiceAudio = null;
    const prefill = wx.getStorageSync("lobster_digital_prefill");
    if (prefill && typeof prefill === "object") {
      wx.removeStorageSync("lobster_digital_prefill");
      this.setData({ title: String(prefill.title || ""), text: String(prefill.text || "") });
    }
    this.loadProfiles();
    this.loadVoices();
    this.loadHistory();
  },

  onShow() {
    share.showShareMenu();
    app.restoreSession();
    this.loadDhTemplate();
    this.refreshAuth();
    if (this.data.phoneBound) {
      this.loadInstallationId();
      this.loadProfiles();
      this.loadVoices();
    }
  },

  loadDhTemplate() {
    const saved = wx.getStorageSync("lobster_current_template") || wx.getStorageSync("lobster_dh_template");
    if (saved && saved.id) {
      this.setData({
        dhTemplateName: String(saved.name || ""),
        dhTemplateDevice: String(saved.device_name || "账号级"),
        dhTemplateStyleId: String(saved.styleId || "")
      });
    } else {
      this.setData({ dhTemplateName: "", dhTemplateDevice: "", dhTemplateStyleId: "" });
    }
  },

  refreshAuth() {
    const phone = String(app.globalData.phone || "");
    const bound = Boolean(app.globalData.token && phone);
    const masked = phone ? phone.replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2") : "";
    this.setData({ phoneBound: bound, maskedPhone: masked });
    if (!bound) this.setData({ errText: "未登录：请先到「我的」用手机号登录授权" });
  },

  goLogin() {
    wx.switchTab({ url: "/pages/profile/profile" });
  },

  openAudioPicker() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先到「我的」登录授权", icon: "none" });
      return;
    }
    this.setData({ pickerOpen: true, pickerLoading: true, pickerItems: [] });
    app.request({ url: "/api/h5/recorder/files?page=1&page_size=30", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || [])
          .filter((row) => row.status === "completed")
          .map((row) => ({
            id: row.id,
            name: String(row.display_name || row.file_name || "录音"),
            time: String(row.created_at || "").replace("T", " ").slice(0, 16),
            statusText: "已转写"
          }));
        this.setData({ pickerItems: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ pickerLoading: false }));
  },

  closePicker() {
    this.setData({ pickerOpen: false });
  },

  noop() {},

  pickRecording(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.pickerItems[index];
    if (!row || this.data.extracting) return;
    this.setData({ extracting: true, extractStep: 0 });
    if (!this.extractTimer) {
      this.extractTimer = setInterval(() => {
        if (!this.data.extracting) return;
        this.setData({ extractStep: Math.min(2, (this.data.extractStep || 0) + 1) });
      }, 6000);
    }
    app.request({
      method: "POST",
      url: `/api/h5/recorder/files/${row.id}/extract-digital-human`,
      data: {},
      timeout: 240000
    })
      .then((data) => {
        const script = String((data && data.script) || "").trim();
        if (!script) throw new Error("没有生成出文案");
        const title = String((data && data.title) || "").trim();
        const duration = Number((data && data.duration_seconds) || 30);
        const useMaterials = !(data && data.use_materials === false);
        const materials = (((data && data.materials) || []).map((item) => ({
          asset_id: String(item.asset_id || ""),
          type: String(item.type || "image"),
          fileUrl: String(item.fileUrl || ""),
          thumb: thumbUrl(String(item.fileUrl || "")),
          name: String(item.name || "")
        }))).filter((item) => item.asset_id && item.fileUrl);
        const patch = {
          text: script,
          videoTitle: title,
          taskName: title || this.data.taskName,
          durationMode: duration > 30 ? "long" : "short",
          targetDuration: Math.max(10, Math.min(300, duration || 30)),
          pickerOpen: false,
          templateMode: useMaterials ? "template" : "none",
          materials: useMaterials ? materials : [],
          advancedOpen: useMaterials
        };
        const emotionIndex = EMOTION_VALUES.indexOf(String((data && data.emotion) || ""));
        if (emotionIndex >= 0) patch.emotionIndex = emotionIndex;
        const speed = Number(data && data.speed);
        if (speed) { patch.speed = speed; patch.speedText = speed.toFixed(2); }
        const matchWayIndex = MATCH_WAY_VALUES.indexOf(String((data && data.match_way) || ""));
        if (matchWayIndex >= 0) patch.matchWayIndex = matchWayIndex;
        const compositionIndex = this.data.compositionValues.indexOf(String((data && data.composition) || ""));
        if (compositionIndex >= 0) patch.compositionIndex = compositionIndex;
        const preprocessIndex = this.data.preprocessValues.indexOf(String((data && data.preprocess) || ""));
        if (preprocessIndex >= 0) patch.preprocessIndex = preprocessIndex;
        const switchMap = {
          header_switch: "headerSwitch",
          subtitle_switch: "subtitleSwitch",
          keyword_switch: "keywordSwitch",
          material_sound_switch: "materialSoundSwitch",
          watermark_switch: "watermarkSwitch"
        };
        Object.keys(switchMap).forEach((key) => {
          const value = data ? data[key] : undefined;
          if (typeof value === "boolean") patch[switchMap[key]] = value;
        });
        this.setData(patch);
        if (this.data.assetsAll.length) {
          const picked = (useMaterials ? materials : []).map((m) => m.asset_id);
          this.setData({
            assetsAll: this.data.assetsAll.map((row) => Object.assign({}, row, {
              selected: picked.indexOf(row.asset_id) >= 0
            }))
          });
        }
        const templateName = String((data && data.template_name) || "").trim();
        const templateStyleId = String((data && data.template_style_id) || "").trim();
        if (useMaterials) {
          this.applyTemplateById(templateStyleId, templateName);
          if (!this.data.assetsAll.length) this.loadAssets();
        }
        const parts = ["文案"];
        if (title) parts.push("标题");
        if (useMaterials) parts.push(materials.length ? `混剪 ${materials.length} 个素材` : "混剪模板");
        wx.showToast({ title: "已填好：" + parts.join(" + "), icon: "none" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => {
        if (this.extractTimer) { clearInterval(this.extractTimer); this.extractTimer = null; }
        this.setData({ extracting: false, extractStep: 0 });
      });
  },

  applyTemplateById(styleId, name) {
    const apply = () => {
      const rows = this.data.templates;
      let index = -1;
      if (styleId) index = rows.findIndex((row) => String(row.styleId || "") === styleId);
      if (index < 0 && name) index = rows.findIndex((row) => String(row.name || "") === name);
      if (index >= 0) this.setData({ templateIndex: index });
    };
    if (!this.data.templates.length) {
      this.loadTemplates("", 60).then(apply);
      return;
    }
    apply();
  },

  goRecords() {
    wx.navigateTo({ url: "/pages/records/records" });
  },

  goAssets() {
    wx.navigateTo({ url: "/pages/assets/assets" });
  },

  goIpProfile() {
    wx.navigateTo({ url: "/pages/ip-profile/ip-profile" });
  },

  onUnload() {
    this.stopPolling();
    if (this.extractTimer) { clearInterval(this.extractTimer); this.extractTimer = null; }
    if (this.trainTimer) clearInterval(this.trainTimer);
    if (this.cloneTimer) clearInterval(this.cloneTimer);
    if (this.voiceAudio) {
      this.voiceAudio.stop();
      this.voiceAudio.destroy();
      this.voiceAudio = null;
    }
  },

  onPullDownRefresh() {
    Promise.all([this.loadProfiles(), this.loadVoices(), this.loadHistory()])
      .then(() => wx.stopPullDownRefresh())
      .catch(() => wx.stopPullDownRefresh());
  },

  iidHeader() {
    return this.installationId ? { "X-Installation-Id": this.installationId } : {};
  },

  loadInstallationId() {
    if (!app.globalData.token) return Promise.resolve("");
    if (this.installationId) return Promise.resolve(this.installationId);
    return app.request({ url: "/api/mobile/devices", timeout: 20000 })
      .then((data) => {
        const rows = (data && data.online_devices) || [];
        const picked = rows.find((row) => row.online) || rows[0];
        this.installationId = picked ? String(picked.installation_id || "") : "";
        return this.installationId;
      })
      .catch(() => "");
  },

  uploadAudioFile(filePath) {
    return api.uploadFile({
      url: "/api/assets/upload",
      filePath,
      name: "file",
      formData: { media_type: "audio", tags: "tts,miniprogram" },
      header: this.iidHeader(),
      token: app.globalData.token || ""
    }).then((data) => {
      const url = assetUrl((data && (data.source_url || data.url)) || "");
      if (!url) throw new Error("配音上传失败，没有返回地址");
      return url;
    });
  },

  resolveAudioUrl(raw) {
    const value = String(raw || "");
    if (/^https?:\/\//i.test(value)) return Promise.resolve(value);
    if (/^data:audio\//i.test(value)) {
      const path = dataUriToTempFile(value);
      if (!path) return Promise.reject(new Error("配音数据解析失败"));
      return this.uploadAudioFile(path);
    }
    return Promise.reject(new Error("配音返回格式不支持"));
  },

  ensureAuth() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return false;
    }
    return true;
  },

  loadProfiles() {
    if (!app.globalData.token) { this.setData({ errText: "未登录：请先到「我的」用手机号登录授权" }); return Promise.resolve(); }
    this.setData({ profilesLoading: true, errText: "" });
    return app.request({ url: "/api/shanjian-digital-human/profiles", timeout: 30000 })
      .then((data) => {
        const profiles = normalizeProfiles((data && data.items) || []);
        let index = profiles.findIndex((row) => row.is_default && row.ready);
        if (index < 0) index = profiles.findIndex((row) => row.ready);
        if (index < 0 && profiles.length) index = 0;
        this.setData({ profiles, profileIndex: index });
      })
      .catch((err) => {
        const msg = api.errorMessage(err);
        this.setData({ errText: "读取数字人失败：" + msg });
        wx.showToast({ title: msg, icon: "none" });
      })
      .then(() => this.setData({ profilesLoading: false }));
  },

  loadVoices() {
    if (!app.globalData.token) return Promise.resolve();
    this.setData({ voicesLoading: true });
    return app.request({ url: "/api/hifly/my/voice/list?page=1&size=100", timeout: 30000 })
      .then((data) => {
        const rows = normalizeVoices((data && (data.items || data.results)) || []);
        this.setData({ voices: rows, voiceNames: rows.map((r) => r.title), voiceIndex: rows.length ? 0 : -1 });
        if (rows.length) this.applyVoiceDefaults(rows[0]);
      })
      .catch((err) => {
        const msg = api.errorMessage(err);
        this.setData({ errText: "读取声音失败：" + msg });
        wx.showToast({ title: msg, icon: "none" });
      })
      .then(() => this.setData({ voicesLoading: false }));
  },

  applyVoiceDefaults(voice) {
    if (!voice) return;
    const pitch = Math.min(12, Math.max(-12, Number(voice.pitch || 0)));
    const volume = Math.min(2, Math.max(0.1, Number(voice.volume || 1)));
    const speed = Math.min(2, Math.max(0.5, Number(voice.rate || 1)));
    const emotionIdx = Math.max(0, EMOTION_VALUES.indexOf(String(voice.emotion || "happy")));
    this.setData({
      pitch,
      pitchText: String(pitch),
      volume,
      volumeText: volume.toFixed(2),
      speed,
      speedText: speed.toFixed(2),
      emotionIndex: emotionIdx
    });
  },

  chooseTrainFile() {
    const mode = this.data.trainMode;
    wx.chooseMedia({
      count: 1,
      mediaType: [mode === "image" ? "image" : "video"],
      maxDuration: 60,
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.setData({ trainFile: file.tempFilePath, trainFileName: file.fileType === "video" ? "已选视频" : "已选图片" });
      }
    });
  },

  onTrainTitle(e) { this.setData({ trainTitle: e.detail.value }); },
  onTrainAuth(e) { this.setData({ trainAuth: e.detail.value }); },
  toggleTrain() { this.setData({ trainOpen: !this.data.trainOpen }); },

  setTrainMode(e) {
    this.setData({ trainMode: String(e.currentTarget.dataset.mode || "image"), trainFile: "", trainFileName: "" });
  },

  toggleTrainAgree(e) { this.setData({ trainAgree: Boolean(e.detail.value) }); },

  submitTrain() {
    if (!this.ensureAuth()) return;
    const { trainFile, trainMode, trainTitle, trainAuth, trainAgree } = this.data;
    if (!trainFile) return wx.showToast({ title: "先选择素材", icon: "none" });
    if (!String(trainTitle || "").trim()) return wx.showToast({ title: "填写数字人名称", icon: "none" });
    if (String(trainAuth || "").trim().length < 2) return wx.showToast({ title: "填写授权说明（至少 2 字）", icon: "none" });
    if (!trainAgree) return wx.showToast({ title: "请先勾选肖像授权", icon: "none" });
    this.setData({ trainSubmitting: true, trainStatus: "上传素材中…" });
    wx.showLoading({ title: "上传中" });
    api.uploadFile({
      url: "/api/assets/upload",
      filePath: trainFile,
      name: "file",
      formData: { media_type: trainMode === "image" ? "image" : "video", tags: "digital_human_v2,profile_train" },
      token: app.globalData.token || ""
    })
      .then((data) => {
        const url = assetUrl((data && (data.source_url || data.url)) || "");
        if (!url) throw new Error("素材上传失败，没有返回地址");
        this.setData({ trainStatus: "提交训练任务…" });
        const body = {
          title: String(trainTitle).trim(),
          mode: trainMode,
          auth_text: String(trainAuth).trim(),
          make_default: true
        };
        if (trainMode === "image") body.image_url = url; else body.video_url = url;
        return app.request({ method: "POST", url: "/api/shanjian-digital-human/profile/train", data: body, timeout: 120000 });
      })
      .then((data) => {
        const taskId = String((data && (data.task_id || (data.record && data.record.task_id))) || "").trim();
        this.setData({ trainStatus: "训练已提交" + (taskId ? "，任务 " + taskId.slice(0, 8) : "") + "，等待出结果…" });
        if (taskId) this.startTrainPolling(taskId);
      })
      .catch((err) => {
        this.setData({ trainStatus: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => {
        this.setData({ trainSubmitting: false });
        wx.hideLoading();
      });
  },

  startTrainPolling(taskId) {
    if (this.trainTimer) clearInterval(this.trainTimer);
    let count = 0;
    const tick = () => {
      count += 1;
      app.request({ method: "POST", url: "/api/shanjian-digital-human/profile/task", data: { task_id: taskId }, timeout: 60000 })
        .then((data) => {
          const status = String((data && data.status) || "processing");
          const text = String((data && data.status_text) || "训练中");
          this.setData({ trainStatus: "训练" + text });
          if (status === "succeed" || status === "failed") {
            clearInterval(this.trainTimer);
            this.trainTimer = null;
            if (status === "succeed") {
              wx.showToast({ title: "训练完成", icon: "success" });
              this.loadProfiles();
              this.setData({ trainOpen: false, trainStatus: "" });
            } else {
              wx.showToast({ title: (data && data.error_message) || "训练失败", icon: "none" });
            }
          }
        })
        .catch((err) => {
          clearInterval(this.trainTimer);
          this.trainTimer = null;
          this.setData({ trainStatus: api.errorMessage(err) });
        });
      if (count > TRAIN_MAX_POLLS) {
        clearInterval(this.trainTimer);
        this.trainTimer = null;
        this.setData({ trainStatus: "训练时间较长，请稍后到列表查看" });
      }
    };
    tick();
    this.trainTimer = setInterval(tick, TRAIN_POLL_MS);
  },

  toggleClone() { this.setData({ cloneOpen: !this.data.cloneOpen }); },

  chooseCloneFile() {
    wx.chooseMessageFile({
      count: 1, type: "file", extension: ["mp3", "wav", "m4a", "aac"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.setData({ cloneFile: file.path, cloneFileName: String(file.name || "已选音频") });
      }
    });
  },

  onCloneTitle(e) { this.setData({ cloneTitle: e.detail.value }); },
  pickCloneLang(e) { this.setData({ cloneLangIndex: Number(e.detail.value || 0) }); },
  toggleCloneAgree(e) { this.setData({ cloneAgree: Boolean(e.detail.value) }); },

  submitClone() {
    if (!this.ensureAuth()) return;
    const { cloneFile, cloneTitle, cloneAgree, cloneLangIndex } = this.data;
    if (!String(cloneTitle || "").trim()) return wx.showToast({ title: "填写声音名称", icon: "none" });
    if (!cloneFile) return wx.showToast({ title: "先选择音频", icon: "none" });
    if (!cloneAgree) return wx.showToast({ title: "请先勾选声音授权", icon: "none" });
    this.setData({ cloneSubmitting: true, cloneStatus: "上传音频…" });
    wx.showLoading({ title: "上传中" });
    api.uploadFile({
      url: "/api/hifly/my/voice/create-upload",
      filePath: cloneFile,
      name: "file",
      formData: {
        title: String(cloneTitle).trim(),
        languages: CLONE_LANG_VALUES[cloneLangIndex] || "zh",
        voice_type: "8"
      },
      token: app.globalData.token || "",
      timeout: 180000
    })
      .then((data) => {
        const taskId = String((data && (data.task_id || (data.item && data.item.task_id))) || "").trim();
        this.setData({ cloneStatus: "克隆任务已提交，等待结果…" });
        if (taskId) this.startClonePolling(taskId);
      })
      .catch((err) => {
        this.setData({ cloneStatus: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => {
        this.setData({ cloneSubmitting: false });
        wx.hideLoading();
      });
  },

  startClonePolling(taskId) {
    if (this.cloneTimer) clearInterval(this.cloneTimer);
    let count = 0;
    const tick = () => {
      count += 1;
      app.request({ method: "POST", url: "/api/hifly/my/voice/task", data: { task_id: taskId }, timeout: 60000 })
        .then((data) => {
          const status = String((data && (data.status || (data.item && data.item.status))) || "processing");
          this.setData({ cloneStatus: "克隆中：" + status });
          if (status === "success" || status === "succeed" || status === "failed") {
            clearInterval(this.cloneTimer);
            this.cloneTimer = null;
            if (status === "failed") {
              wx.showToast({ title: "克隆失败", icon: "none" });
            } else {
              wx.showToast({ title: "克隆完成", icon: "success" });
              this.setData({ cloneOpen: false, cloneStatus: "" });
              this.loadVoices();
            }
          }
        })
        .catch((err) => {
          clearInterval(this.cloneTimer);
          this.cloneTimer = null;
          this.setData({ cloneStatus: api.errorMessage(err) });
        });
      if (count > TRAIN_MAX_POLLS) {
        clearInterval(this.cloneTimer);
        this.cloneTimer = null;
        this.setData({ cloneStatus: "克隆时间较长，请稍后刷新列表" });
      }
    };
    tick();
    this.cloneTimer = setInterval(tick, TRAIN_POLL_MS);
  },

  audition() {
    const voice = this.data.voices[this.data.voiceIndex];
    if (!voice) return;
    const payload = {
      voice: voice.voice,
      text: "你好，这是当前声音的试听效果。",
      rate: String(this.data.speed || 1),
      volume: String(this.data.volume || 1),
      pitch: String(this.data.pitch),
      emotion: EMOTION_VALUES[this.data.emotionIndex] || "happy"
    };
    if (String(this.data.instructions || "").trim()) ttsPayload.instructions = String(this.data.instructions).trim();
    if (voice.provider === "minimax" || voice.provider === "qwen") payload.voice_provider = voice.provider;
    wx.showLoading({ title: "试听生成" });
    app.request({ method: "POST", url: "/api/hifly/my/voice/preview-tts", data: payload, header: this.iidHeader(), timeout: 120000 })
      .then((data) => {
        const url = findAudioUrl(data);
        if (!url) throw new Error("没有返回试听音频");
        const playable = /^data:audio\//i.test(url) ? dataUriToTempFile(url) : url;
        if (!playable) throw new Error("试听音频解析失败");
        if (!this.voiceAudio) this.voiceAudio = wx.createInnerAudioContext();
        this.voiceAudio.src = playable;
        this.voiceAudio.play();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  loadTemplates(keyword, pageSize) {
    this.setData({ templatesLoading: true });
    const body = { page_size: pageSize || this.data.templatePageSize || 20, scene: "realMan" };
    const kw = String(keyword !== undefined ? keyword : this.data.templateKeyword || "").trim();
    if (kw) { body.search_key = "name"; body.search_value = kw; }
    return app.request({ method: "POST", url: "/api/shanjian-smart-clip/templates", data: body, timeout: 60000 })
      .then((data) => {
        const rows = ((data && data.results) || []).map((row, i) => ({
          styleId: String(row.styleId || row.style_id || row.templateId || row.id || ""),
          name: String(row.name || row.title || `模板 ${i + 1}`).slice(0, 20),
          cover: assetUrl(row.cover || row.coverUrl || row.cover_url || "")
        })).filter((row) => row.styleId);
        this.setData({ templates: rows, templateIndex: rows.length ? 0 : -1 });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ templatesLoading: false }));
  },

  onTemplateKeyword(e) {
    const keyword = e.detail.value;
    this.setData({ templateKeyword: keyword });
    if (this.templateTimer) clearTimeout(this.templateTimer);
    this.templateTimer = setTimeout(() => this.loadTemplates(keyword), 500);
  },

  loadMoreTemplates() {
    const size = Math.min(60, (this.data.templatePageSize || 20) + 20);
    this.setData({ templatePageSize: size });
    this.loadTemplates(this.data.templateKeyword, size);
  },

  loadAssets() {
    if (!app.globalData.token) return Promise.resolve();
    this.setData({ assetsLoading: true });
    return app.request({ url: "/api/assets?limit=120", timeout: 30000 })
      .then((data) => {
        const rows = ((data && (data.assets || data.items)) || []).map((row) => {
          const id = String(row.asset_id || row.id || "");
          if (!id) return null;
          const type = String(row.media_type || "").toLowerCase();
          if (type !== "image" && type !== "video") return null;
          return {
            asset_id: id,
            media_type: type,
            name: String(row.filename || row.name || id),
            url: assetUrl(row.source_url || row.url || row.open_url || ""),
            thumb: thumbUrl(assetUrl(row.source_url || row.url || row.open_url || "")),
            selected: this.data.materials.some((m) => m.asset_id === id)
          };
        }).filter(Boolean);
        this.setData({ assetsAll: rows });
        this.applyAssetFilter(rows, this.data.assetTab, this.data.assetKeyword);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ assetsLoading: false }));
  },

  applyAssetFilter(all, tab, keyword) {
    const kw = String(keyword || "").trim().toLowerCase();
    const rows = (all || []).filter((row) => row.media_type === tab)
      .filter((row) => !kw || row.name.toLowerCase().indexOf(kw) >= 0);
    this.setData({ assets: rows });
  },

  onAssetKeyword(e) {
    const keyword = e.detail.value;
    this.setData({ assetKeyword: keyword });
    this.applyAssetFilter(this.data.assetsAll, this.data.assetTab, keyword);
  },

  switchAssetTab(e) {
    const tab = String(e.currentTarget.dataset.tab || "image");
    this.setData({ assetTab: tab });
    if (!this.data.assetsAll.length) this.loadAssets();
    else this.applyAssetFilter(this.data.assetsAll, tab, this.data.assetKeyword);
  },

  previewMedia(url, type) {
    if (!url) return wx.showToast({ title: "没有可预览的资源", icon: "none" });
    const kind = type === "video" ? "video" : "image";
    const target = kind === "video" ? playUrl(url) : thumbUrl(url);
    if (wx.previewMedia) {
      wx.previewMedia({ sources: [{ url: target, type: kind }], current: 0 });
      return;
    }
    if (kind === "image") wx.previewImage({ current: target, urls: [target] });
    else wx.showToast({ title: "当前版本不支持视频预览", icon: "none" });
  },

  previewProfile(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.profiles[index];
    if (!row) return;
    this.previewMedia(row.cover_url || row.source_url, row.kind);
  },

  previewAsset(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.assets[index];
    if (!row) return;
    this.previewMedia(row.url, row.media_type === "video" ? "video" : "image");
  },

  previewMaterial(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.materials[index];
    if (!row) return;
    this.previewMedia(row.fileUrl, row.type === "video" ? "video" : "image");
  },

  selectProfile(e) { this.setData({ profileIndex: Number(e.currentTarget.dataset.index || 0) }); },

  selectVoice(e) {
    const index = Number(e.detail.value || 0);
    this.setData({ voiceIndex: index });
    this.applyVoiceDefaults(this.data.voices[index]);
  },

  selectTemplate(e) { this.setData({ templateIndex: Number(e.currentTarget.dataset.index || 0) }); },

  toggleTemplate(e) {
    this.setTemplateMode({ currentTarget: { dataset: { mode: Boolean(e.detail.value) ? "template" : "none" } } });
  },

  onSwitch(e) {
    const key = e.currentTarget.dataset.key;
    if (key) this.setData({ [key]: Boolean(e.detail.value) });
  },

  pickMatchWay(e) { this.setData({ matchWayIndex: Number(e.detail.value || 0) }); },
  pickEmotion(e) { this.setData({ emotionIndex: Number(e.detail.value || 0) }); },
  onInstructions(e) { this.setData({ instructions: e.detail.value }); },
  pickPreprocess(e) { this.setData({ preprocessIndex: Number(e.detail.value || 0) }); },
  pickComposition(e) { this.setData({ compositionIndex: Number(e.detail.value || 0) }); },
  setMode(e) { this.setData({ mode: String(e.currentTarget.dataset.mode || "tts") }); },
  onTaskName(e) { this.setData({ taskName: e.detail.value }); },
  onVideoTitle(e) { this.setData({ videoTitle: e.detail.value }); },
  setDurationMode(e) { this.setData({ durationMode: String(e.currentTarget.dataset.mode || "short") }); },
  onTargetDuration(e) { this.setData({ targetDuration: Number(e.detail.value || 60) }); },
  setTemplateMode(e) {
    const mode = String(e.currentTarget.dataset.mode || "none");
    this.setData({ templateMode: mode });
    if (mode === "template") {
      if (!this.data.templates.length) this.loadTemplates();
      if (!this.data.assetsAll.length) this.loadAssets();
    }
  },
  toggleAdvanced() { this.setData({ advancedOpen: !this.data.advancedOpen }); },
  onTitleInput(e) { this.setData({ title: e.detail.value }); },
  onTextInput(e) { this.setData({ text: e.detail.value }); },
  onSpeedChange(e) { const v = Number(e.detail.value || 100) / 100; this.setData({ speed: v, speedText: v.toFixed(2) }); },
  onVolumeChange(e) { const v = Number(e.detail.value || 100) / 100; this.setData({ volume: v, volumeText: v.toFixed(2) }); },
  onPitchChange(e) { const v = Number(e.detail.value); this.setData({ pitch: v, pitchText: String(v) }); },

  toggleAsset(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const asset = this.data.assets[index];
    if (!asset) return;
    const exists = this.data.materials.some((m) => m.asset_id === asset.asset_id);
    let materials = this.data.materials.slice();
    if (exists) {
      materials = materials.filter((m) => m.asset_id !== asset.asset_id);
    } else {
      if (materials.length >= 20) return wx.showToast({ title: "最多 20 个素材", icon: "none" });
      materials.push({ asset_id: asset.asset_id, type: asset.media_type, fileUrl: asset.url, thumb: asset.thumb, name: asset.name });
    }
    const all = this.data.assetsAll.map((row) =>
      row.asset_id === asset.asset_id ? Object.assign({}, row, { selected: !exists }) : row
    );
    this.setData({ materials, assetsAll: all });
    this.applyAssetFilter(all, this.data.assetTab, this.data.assetKeyword);
  },

  removeMaterial(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const target = this.data.materials[index];
    if (!target) return;
    const materials = this.data.materials.filter((m) => m.asset_id !== target.asset_id);
    const all = this.data.assetsAll.map((row) =>
      row.asset_id === target.asset_id ? Object.assign({}, row, { selected: false }) : row
    );
    this.setData({ materials, assetsAll: all });
    this.applyAssetFilter(all, this.data.assetTab, this.data.assetKeyword);
  },

  clearMaterials() {
    const all = this.data.assetsAll.map((row) => Object.assign({}, row, { selected: false }));
    this.setData({ materials: [], assetsAll: all });
    this.applyAssetFilter(all, this.data.assetTab, this.data.assetKeyword);
  },

  uploadMaterial() {
    wx.chooseMedia({
      count: 1, mediaType: ["image", "video"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        const type = file.fileType === "video" ? "video" : "image";
        wx.showLoading({ title: "上传中" });
        api.uploadFile({
          url: "/api/assets/upload",
          filePath: file.tempFilePath,
          name: "file",
          formData: { media_type: type, tags: "digital_human_v2,material" },
          token: app.globalData.token || ""
        })
          .then((data) => {
            const id = String((data && data.asset_id) || "").trim();
            const url = assetUrl((data && (data.source_url || data.url)) || "");
            if (!id || !url) throw new Error("上传成功但没有返回素材编号");
            if (this.data.materials.some((m) => m.asset_id === id)) return;
            this.setData({
              materials: this.data.materials.concat({
                asset_id: id, type, fileUrl: url, thumb: thumbUrl(url), name: String((data && data.filename) || id)
              })
            });
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  chooseAudio() {
    wx.chooseMessageFile({
      count: 1, type: "file", extension: ["mp3", "wav", "m4a", "aac"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        wx.showLoading({ title: "上传音频" });
        api.uploadFile({
          url: "/api/assets/upload",
          filePath: file.path,
          name: "file",
          formData: { media_type: "audio", tags: "digital_human_v2,audio_drive" },
          token: app.globalData.token || ""
        })
          .then((data) => {
            const url = assetUrl((data && (data.source_url || data.url)) || "");
            if (!url) throw new Error("上传成功但没有返回音频地址");
            this.setData({ audioUrl: url, audioName: String(file.name || "已选音频") });
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  clearAudio() { this.setData({ audioUrl: "", audioName: "" }); },

  buildCreateBody(profile, title, audioUrl) {
    const useTemplate = this.data.templateMode === "template";
    const body = {
      profile_id: profile.id,
      title,
      template_scene: useTemplate ? "realMan" : "",
      language: "zh-CN",
      speed_ratio: Number(this.data.speed || 1),
      audio_url: audioUrl,
      long_video: this.data.durationMode === "long",
      video_duration: this.data.durationMode === "long" ? Number(this.data.targetDuration || 60) : 30
    };
    if (this.data.mode === "tts") body.text = String(this.data.text || "").trim();
    if (useTemplate) {
      const tpl = this.data.templates[this.data.templateIndex];
      if (tpl) {
        body.style_id = tpl.styleId;
        body.header_switch = Boolean(this.data.headerSwitch);
        body.subtitle_switch = Boolean(this.data.subtitleSwitch);
        body.keyword_switch = Boolean(this.data.keywordSwitch);
        body.material_switch = Boolean(this.data.materialSwitch);
        body.material_sound_switch = Boolean(this.data.materialSoundSwitch);
        body.material_match_way = MATCH_WAY_VALUES[this.data.matchWayIndex] || "fuzzyMatch";
        body.resource_preprocess_method = this.data.preprocessValues[this.data.preprocessIndex] || "roughCut";
        body.material_composition = this.data.compositionValues[this.data.compositionIndex] || "random";
        body.watermark_show = Boolean(this.data.watermarkSwitch);
        if (this.data.materials.length) {
          body.materials = this.data.materials.map((m) => ({
            asset_id: m.asset_id, type: m.type, fileUrl: m.fileUrl, name: m.name
          }));
        }
      }
    }
    if (!body.style_id && this.data.dhTemplateStyleId) {
      body.style_id = this.data.dhTemplateStyleId;
      body.template_scene = "realMan";
    }
    return body;
  },

  generate() {
    if (!this.ensureAuth()) return;
    if (!this.installationId) {
      this.setData({ statusText: "读取设备信息…" });
      this.loadInstallationId().then(() => this.doGenerate()).catch(() => this.doGenerate());
      return;
    }
    this.doGenerate();
  },

  doGenerate() {
    const profile = this.data.profiles[this.data.profileIndex];
    const voice = this.data.voices[this.data.voiceIndex];
    if (!profile || !profile.ready) return wx.showToast({ title: "请选择已训练完成的数字人", icon: "none" });
    if (this.data.mode === "tts" && !voice) return wx.showToast({ title: "请选择声音", icon: "none" });
    if (this.data.mode === "tts" && !String(this.data.text || "").trim()) {
      return wx.showToast({ title: "请填写口播文案", icon: "none" });
    }
    if (this.data.mode === "audio" && !this.data.audioUrl) return wx.showToast({ title: "请上传驱动音频", icon: "none" });

    const title = String(this.data.videoTitle || "").trim() ||
      String(this.data.taskName || "").trim() ||
      (this.data.mode === "tts" ? String(this.data.text || "").slice(0, 24) : "数字人口播");
    this.lastTitle = title;
    this.setData({ submitting: true, statusText: "准备中…", videoUrl: "", coverUrl: "" });

    if (this.data.mode === "audio") {
      this.prepareThenCreate(profile, title, this.data.audioUrl);
      return;
    }

    this.setData({ statusText: "正在合成配音…" });
    const ttsPayload = {
      voice: voice.voice,
      text: String(this.data.text || "").trim(),
      rate: String(this.data.speed || 1),
      volume: String(this.data.volume || 1),
      pitch: String(this.data.pitch),
      emotion: EMOTION_VALUES[this.data.emotionIndex] || "happy"
    };
    if (String(this.data.instructions || "").trim()) ttsPayload.instructions = String(this.data.instructions).trim();
    if (voice.provider === "minimax" || voice.provider === "qwen") ttsPayload.voice_provider = voice.provider;

    app.request({ method: "POST", url: "/api/hifly/my/voice/preview-tts", data: ttsPayload, header: this.iidHeader(), timeout: 300000 })
      .then((data) => {
        const audioUrl = findAudioUrl(data);
        if (!audioUrl) throw new Error("配音生成失败，没有返回音频地址");
        this.setData({ statusText: "正在上传配音…" });
        return this.resolveAudioUrl(audioUrl).then((finalUrl) => {
          this.setData({ statusText: "正在提交生成任务…" });
          this.prepareThenCreate(profile, title, finalUrl);
        });
      })
      .catch((err) => {
        this.setData({ statusText: "", submitting: false });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  prepareThenCreate(profile, title, audioUrl) {
    const body = this.buildCreateBody(profile, title, audioUrl);
    const ids = (body.materials || []).map((m) => m.asset_id);
    if (!ids.length) {
      this.submitCreate(body);
      return;
    }
    app.request({ method: "POST", url: "/api/assets/lookup", data: { asset_ids: ids }, header: this.iidHeader(), timeout: 30000 })
      .then((data) => {
        const missing = ((data && data.missing) || []).map(String);
        if (missing.length) {
          const kept = this.data.materials.filter((m) => missing.indexOf(m.asset_id) < 0);
          this.setData({ materials: kept });
          body.materials = body.materials.filter((m) => missing.indexOf(m.asset_id) < 0);
          wx.showToast({ title: `${missing.length} 个素材已被删除，已移除`, icon: "none" });
        }
        this.submitCreate(body);
      })
      .catch(() => this.submitCreate(body));
  },

  submitCreate(body) {
    app.request({ method: "POST", url: "/api/shanjian-digital-human/video/create", data: body, header: this.iidHeader(), timeout: 180000 })
      .then((data) => {
        const taskId = String((data && (data.task_id || (data.record && data.record.task_id))) || "").trim();
        if (!taskId) throw new Error("没有拿到任务号");
        this.setData({ statusText: "已提交，排队生成中…", submitting: false });
        this.startPolling(taskId);
      })
      .catch((err) => {
        this.setData({ statusText: "", submitting: false });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  startPolling(taskId) {
    this.stopPolling();
    this.pollCount = 0;
    const tick = () => this.poll(taskId);
    tick();
    this.pollTimer = setInterval(tick, POLL_MS);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  poll(taskId) {
    this.pollCount += 1;
    if (this.pollCount > MAX_POLLS) {
      this.stopPolling();
      this.setData({ statusText: "生成超时，请稍后到作品里查看" });
      return;
    }
    app.request({ method: "POST", url: "/api/shanjian-digital-human/video/task", data: { task_id: taskId }, header: this.iidHeader(), timeout: 60000 })
      .then((data) => {
        const status = String((data && data.status) || "processing");
        const statusText = String((data && data.status_text) || "处理中");
        const videoUrl = assetUrl((data && data.video_url) || "");
        const coverUrl = assetUrl((data && data.cover_url) || "");
        this.setData({ statusText, videoUrl, coverUrl });
        if (status === "succeed" || status === "failed") {
          this.stopPolling();
          if (status === "succeed") {
            wx.showToast({ title: "生成完成", icon: "success" });
            this.saveToWorks(videoUrl);
            this.loadHistory();
          } else {
            wx.showToast({ title: (data && data.message) || "生成失败", icon: "none" });
          }
        }
      })
      .catch((err) => {
        this.stopPolling();
        this.setData({ statusText: api.errorMessage(err) });
      });
  },

  saveToWorks(url) {
    if (!url) return;
    app.request({
      method: "POST",
      url: "/api/assets/save-url",
      data: { url, media_type: "video", tags: "digital_human_v2,miniprogram", title: this.lastTitle || "数字人口播" },
      timeout: 180000
    })
      .then(() => wx.setStorageSync("lobster_refresh_works", "1"))
      .catch(() => {});
  },

  loadHistory() {
    if (!app.globalData.token) return Promise.resolve();
    return app.request({ url: "/api/shanjian-digital-human/videos", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).slice(0, 8).map((row) => ({
          id: String(row.id || row.task_id || ""),
          title: String(row.title || "数字人口播"),
          status_text: String(row.status_text || row.status || ""),
          created_at: String(row.created_at || "").replace("T", " ").slice(0, 16),
          video_url: assetUrl(row.video_url || ""),
          cover_url: assetUrl(row.cover_url || "")
        }));
        this.setData({ history: rows });
      })
      .catch(() => {});
  },

  downloadVideo() {
    const url = this.data.videoUrl;
    if (!url) return;
    wx.showLoading({ title: "下载中" });
    wx.downloadFile({
      url,
      success: (res) => {
        if (res.statusCode !== 200) return wx.showToast({ title: "下载失败", icon: "none" });
        wx.saveVideoToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => wx.showToast({ title: "已保存到相册", icon: "success" }),
          fail: () => wx.showToast({ title: "保存失败，请检查相册权限", icon: "none" })
        });
      },
      fail: () => wx.showToast({ title: "下载失败", icon: "none" }),
      complete: () => wx.hideLoading()
    });
  },

  openHistory(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.history[index];
    if (!row) return;
    this.setData({ videoUrl: row.video_url, coverUrl: row.cover_url, statusText: row.status_text });
  },

  copyVideoUrl() {
    if (!this.data.videoUrl) return;
    wx.setClipboardData({ data: this.data.videoUrl });
  }
});