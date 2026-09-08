const serverState = document.getElementById("serverState");
const REMOTE_BASE = window.location.pathname.startsWith("/remote") ? "/remote" : "";
const initialRemoteQuery = new URLSearchParams(window.location.search);
const queryAdminSession = initialRemoteQuery.get("admin_session");
if (queryAdminSession) {
  localStorage.setItem("bhzn_session_token", queryAdminSession);
  history.replaceState({}, document.title, `${REMOTE_BASE || "/"}`);
}
const loginView = document.getElementById("loginView");
const appShell = document.getElementById("appShell");
const authForm = document.getElementById("authForm");
const loginModeBtn = document.getElementById("loginModeBtn");
const registerModeBtn = document.getElementById("registerModeBtn");
const usernameInput = document.getElementById("usernameInput");
const passwordInput = document.getElementById("passwordInput");
const authSubmitBtn = document.getElementById("authSubmitBtn");
const authHint = document.getElementById("authHint");
const accountName = document.getElementById("accountName");
const accountStatus = document.getElementById("accountStatus");
const accountMenuBtn = document.getElementById("accountMenuBtn");
const accountDropdown = document.getElementById("accountDropdown");
const avatarInitial = document.getElementById("avatarInitial");
const logoutBtn = document.getElementById("logoutBtn");
const addForm = document.getElementById("addForm");
const deviceIdInput = document.getElementById("deviceIdInput");
const deviceCodeInput = document.getElementById("deviceCodeInput");
const bindHint = document.getElementById("bindHint");
const fileTransferForm = document.getElementById("fileTransferForm");
const fileDeviceSelect = document.getElementById("fileDeviceSelect");
const fileInput = document.getElementById("fileInput");
const fileSubmitBtn = document.getElementById("fileSubmitBtn");
const fileHint = document.getElementById("fileHint");
const fileTransferLog = document.getElementById("fileTransferLog");
const deviceShelf = document.getElementById("deviceShelf");
const activeTitle = document.getElementById("activeTitle");
const activeMeta = document.getElementById("activeMeta");
const controlTabBtn = document.getElementById("controlTabBtn");
const wallTabBtn = document.getElementById("wallTabBtn");
const filesTabBtn = document.getElementById("filesTabBtn");
const stageBody = document.querySelector(".stage-body");
const screenWorkspace = document.getElementById("screenWorkspace");
const viewerWrap = document.getElementById("viewerWrap");
const screenPlaceholder = document.getElementById("screenPlaceholder");
const screenWall = document.getElementById("screenWall");
const fileTransferWorkspace = document.getElementById("fileTransferWorkspace");
const fileTransferSummary = document.getElementById("fileTransferSummary");
const fileTransferTable = document.getElementById("fileTransferTable");
const fileTransferEmpty = document.getElementById("fileTransferEmpty");
const screenWallGrid = document.getElementById("screenWallGrid");
const screenWallEmpty = document.getElementById("screenWallEmpty");
const wallControlWindow = document.getElementById("wallControlWindow");
const wallControlTitle = document.getElementById("wallControlTitle");
const wallControlMeta = document.getElementById("wallControlMeta");
const wallControlViewer = document.getElementById("wallControlViewer");
const wallControlCanvas = document.getElementById("wallControlCanvas");
const wallControlCtx = wallControlCanvas.getContext("2d");
const wallControlFullscreenBtn = document.getElementById("wallControlFullscreenBtn");
const wallControlCloseBtn = document.getElementById("wallControlCloseBtn");
const wallControlBackBtn = document.getElementById("wallControlBackBtn");
const wallControlHomeBtn = document.getElementById("wallControlHomeBtn");
const wallControlHomeSwipeBtn = document.getElementById("wallControlHomeSwipeBtn");
const wallResizeHandles = Array.from(document.querySelectorAll("[data-wall-resize]"));
const viewer = document.getElementById("viewer");
const rtcVideo = document.getElementById("rtcVideo");
const canvas = document.getElementById("screenCanvas");
const ctx = canvas.getContext("2d");
const remoteCursor = document.createElement("div");
remoteCursor.className = "remote-cursor hidden";
viewer.appendChild(remoteCursor);
rtcVideo.addEventListener("loadedmetadata", () => {
  if (!rtcState || !rtcVideo.videoWidth || !rtcVideo.videoHeight) return;
  canvas.width = rtcVideo.videoWidth;
  canvas.height = rtcVideo.videoHeight;
  frameSize = { width: rtcVideo.videoWidth, height: rtcVideo.videoHeight };
  viewer.style.aspectRatio = `${rtcVideo.videoWidth} / ${rtcVideo.videoHeight}`;
});
const closeScreenBtn = document.getElementById("closeScreenBtn");
const qualitySelect = document.getElementById("qualitySelect");
const fullscreenBtn = document.getElementById("fullscreenBtn");
const backBtn = document.getElementById("backBtn");
const homeBtn = document.getElementById("homeBtn");
const homeSwipeBtn = document.getElementById("homeSwipeBtn");
const stopBtn = document.getElementById("stopBtn");
const binaryFrameMagic = "BHZF1";
const textDecoder = new TextDecoder();

let ws;
let reconnectTimer = 0;
let wsGeneration = 0;
let authMode = "login";
let sessionToken = localStorage.getItem("bhzn_session_token") || "";
let currentUser = null;
let devices = [];
let activeDeviceId = "";
let sessionId = "";
let screenOpen = false;
let activeStageTab = "control";
const monitorSessions = new Map();
const pendingControlIntents = new Map();
let dragStart = null;
let dragLast = null;
let dragLastSentAt = 0;
let dragMoved = false;
let dragStartedRemote = false;
let dragButton = "left";
let frameSize = { width: 0, height: 0 };
let inputReady = false;
let pendingText = "";
let pendingTextTimer = 0;
let frameDrawSerial = 0;
let lastFrameTimestamp = 0;
const previewFrameSerials = new Map();
const previewFrameTimestamps = new Map();
let wallRenderKey = "";
let wallActiveDeviceId = "";
let wallFrameSize = { width: 0, height: 0 };
let wallInputReady = false;
let wallFrameDrawSerial = 0;
let wallLastFrameTimestamp = 0;
let wallDragStart = null;
let wallDragLast = null;
let wallDragLastSentAt = 0;
let wallDragMoved = false;
let wallDragStartedRemote = false;
let wallDragButton = "left";
let wallResizeState = null;
let wallTileResizeState = null;
let wallPendingText = "";
let wallPendingTextTimer = 0;
let fileTransfers = [];
let inputSerial = 0;
let rtcState = null;
let selectedQualityProfile = localStorage.getItem("bhzn_quality_profile") || "balanced";
const requestedDeviceId = initialRemoteQuery.get("device") || "";
const wallTileSizes = new Map();

const qualityProfiles = {
  data: { profile: "data", label: "省流量", maxSide: 1280, fps: 10, jpegQuality: 42, bitrateKbps: 1200 },
  balanced: { profile: "balanced", label: "平衡", maxSide: 1920, fps: 18, jpegQuality: 58, bitrateKbps: 3000 },
  hd: { profile: "hd", label: "高清", maxSide: 2560, fps: 24, jpegQuality: 66, bitrateKbps: 6000 },
  lan: { profile: "lan", label: "局域网", maxSide: 3840, fps: 30, jpegQuality: 74, bitrateKbps: 12000 }
};

function devicePlatform(device) {
  return String(device?.platform || "android").toLowerCase();
}

function isDesktopDevice(device) {
  return ["windows", "macos", "linux"].includes(devicePlatform(device));
}

function defaultDeviceLabel(device) {
  const platform = devicePlatform(device);
  if (platform === "windows") return "Windows Device";
  if (platform === "macos") return "Mac Device";
  if (platform === "linux") return "Linux Device";
  if (platform === "ios") return "iPhone Device";
  return "Android Device";
}

function deviceName(device) {
  return device?.label || device?.name || defaultDeviceLabel(device);
}

function hasScreenPermission(device) {
  return Boolean(device?.permissions?.mediaProjection || device?.permissions?.screenCapture);
}

function hasInputPermission(device) {
  const permissions = device?.permissions || {};
  if (Object.prototype.hasOwnProperty.call(permissions, "inputControl")) {
    return Boolean(permissions.inputControl);
  }
  if (devicePlatform(device) === "android") return false;
  return Boolean(permissions.accessibility);
}

function canControlDevice(device) {
  return Boolean(device?.controlEnabled && hasScreenPermission(device) && hasInputPermission(device));
}

function controlBlockReason(device) {
  if (!device?.online) return "设备不在线";
  if (!hasScreenPermission(device)) return `${platformText(device)} 端屏幕权限未就绪`;
  if (!hasInputPermission(device)) return `${platformText(device)} 端输入服务未就绪`;
  return "";
}

function platformText(device) {
  const platform = devicePlatform(device);
  if (platform === "windows") return "Windows";
  if (platform === "macos") return "macOS";
  if (platform === "linux") return "Linux";
  if (platform === "ios") return "iPhone";
  return "Android";
}

function currentQualityProfile() {
  return qualityProfiles[selectedQualityProfile] || qualityProfiles.balanced;
}

function setQualityProfile(profile, restart = false) {
  selectedQualityProfile = qualityProfiles[profile] ? profile : "balanced";
  localStorage.setItem("bhzn_quality_profile", selectedQualityProfile);
  if (qualitySelect) qualitySelect.value = selectedQualityProfile;
  if (restart && screenOpen && activeDeviceId && ws?.readyState === WebSocket.OPEN) {
    const device = devices.find((item) => item.id === activeDeviceId);
    if (supportsRtc(device)) {
      const previousRelaySessionId = sessionId && rtcState?.sessionId !== sessionId ? sessionId : "";
      if (previousRelaySessionId) {
        ws.send(JSON.stringify({ type: "stop-control", sessionId: previousRelaySessionId }));
      }
      closeRtc("quality_changed", true);
      sessionId = "";
      activeMeta.textContent = `正在切换到${currentQualityProfile().label}档`;
      pendingControlIntents.delete(activeDeviceId);
      requestControlSession(device);
    } else if (sessionId) {
      activeMeta.textContent = "中转模式固定为省流量档";
    }
  }
}

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

function apiPath(path) {
  return REMOTE_BASE && path.startsWith("/api/") ? `${REMOTE_BASE}${path}` : path;
}

async function api(path, options = {}) {
  const headers = {
    "content-type": "application/json",
    ...(options.headers || {})
  };
  if (sessionToken) headers.authorization = `Bearer ${sessionToken}`;
  const response = await fetch(apiPath(path), {
    ...options,
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `http_${response.status}`);
    error.data = data;
    throw error;
  }
  return data;
}

async function apiRaw(path, body, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (sessionToken) headers.authorization = `Bearer ${sessionToken}`;
  const response = await fetch(apiPath(path), {
    method: options.method || "POST",
    headers,
    body
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `http_${response.status}`);
    error.data = data;
    throw error;
  }
  return data;
}

function statusText(status) {
  if (status === "approved") return "已审核";
  if (status === "rejected") return "已拒绝";
  return "待审核";
}

function setAuthMode(mode) {
  authMode = mode;
  loginModeBtn.classList.toggle("active", mode === "login");
  registerModeBtn.classList.toggle("active", mode === "register");
  authSubmitBtn.textContent = mode === "login" ? "登录" : "注册";
  authHint.textContent = mode === "login"
    ? "登录后进入设备控制台。"
    : "注册后需要管理员审核，通过后才能进入控制台。";
}

function showLogin(message) {
  loginView.classList.remove("hidden");
  appShell.classList.add("hidden");
  if (message) authHint.textContent = message;
}

function showApp() {
  loginView.classList.add("hidden");
  appShell.classList.remove("hidden");
}

function setUser(user) {
  currentUser = user;
  const approved = user?.status === "approved";
  accountName.textContent = user?.username || "-";
  accountStatus.textContent = user ? statusText(user.status) : "-";
  avatarInitial.textContent = user?.username ? user.username.slice(0, 1).toUpperCase() : "-";
  serverState.textContent = user
    ? (approved ? "账号已审核" : statusText(user.status))
    : "请先登录";

  if (approved) {
    showApp();
    return;
  }

  closeSocket();
  devices = [];
  clearMonitorSessions(false, true);
  sessionId = "";
  activeDeviceId = "";
  closeScreen(false);
  setInputReady(false);
  renderDevices();
  showLogin(user ? (user.status === "pending" ? "账号已注册，正在等待管理员审核。" : "账号未通过审核，请联系管理员。") : "");
}

function closeSocket() {
  wsGeneration += 1;
  clearTimeout(reconnectTimer);
  closeRtc("socket_closed", false);
  if (ws) {
    ws.close();
    ws = null;
  }
}

function connect() {
  if (!sessionToken || currentUser?.status !== "approved") return;
  clearTimeout(reconnectTimer);
  closeSocket();
  const socketGeneration = ++wsGeneration;
  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";
    ws.addEventListener("open", () => {
      if (socketGeneration !== wsGeneration) return;
      sessionId = "";
      clearMonitorSessions(false, true);
      setInputReady(false);
      ws.send(JSON.stringify({ type: "hello-controller", token: sessionToken }));
    });
  ws.addEventListener("close", () => {
    if (socketGeneration !== wsGeneration) return;
    if (!sessionToken || currentUser?.status !== "approved") return;
    sessionId = "";
    setInputReady(false);
    if (screenOpen) activeMeta.textContent = "连接断开，正在重连";
    reconnectTimer = setTimeout(connect, 1200);
  });
  ws.addEventListener("message", (event) => {
    if (socketGeneration !== wsGeneration) return;
    if (typeof event.data !== "string") {
      handleBinaryFrame(event.data);
      return;
    }
    const msg = JSON.parse(event.data);
    if (msg.type === "devices") {
      devices = msg.devices || [];
      syncActiveDeviceState();
      renderDevices();
      syncMonitorSessions();
      renderScreenWall();
      resumeActiveControl();
    }
    if (msg.type === "file-transfer" && msg.transfer) {
      upsertFileTransfer(msg.transfer);
    }
    if (msg.type === "control-ready") {
      const intent = pendingControlIntents.get(msg.device.id);
      pendingControlIntents.delete(msg.device.id);
      if (intent === "monitor") {
        monitorSessions.set(msg.device.id, msg.sessionId);
        renderScreenWall();
        return;
      }
      if (msg.device.id !== activeDeviceId || !screenOpen) {
        if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop-control", sessionId: msg.sessionId }));
        return;
      }
      if (rtcState?.deviceId === msg.device.id) {
        if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop-control", sessionId: msg.sessionId }));
        return;
      }
      sessionId = msg.sessionId;
      activeDeviceId = msg.device.id;
      openScreen();
      setActiveDevice(msg.device);
      setInputReady(canControlDevice(msg.device));
      stopBtn.disabled = false;
      canvas.focus({ preventScroll: true });
      const relayExpire = Number(msg.ttlSeconds || 0);
      activeMeta.textContent = canControlDevice(msg.device)
        ? `${deviceName(msg.device)} · WebSocket 中转${relayExpire ? ` · ${Math.floor(relayExpire / 60)} 分钟限时` : ""}`
        : `已进入画面，仅可观看：${controlBlockReason(msg.device) || "控制未就绪"}`;
    }
    if (msg.type === "rtc-ready") {
      handleRtcReady(msg);
    }
    if (msg.type === "rtc-answer") {
      handleRtcAnswer(msg);
    }
    if (msg.type === "rtc-ice-candidate") {
      handleRtcRemoteCandidate(msg);
    }
    if (msg.type === "rtc-state") {
      handleRtcPeerState(msg);
    }
    if (msg.type === "rtc-stopped") {
      handleRtcStopped(msg);
    }
    if (msg.type === "frame") {
      handleTextFrame(msg);
    }
    if (msg.type === "control-stopped") {
      const monitorDeviceId = deviceIdByMonitorSession(msg.sessionId);
      if (monitorDeviceId) {
        monitorSessions.delete(monitorDeviceId);
        renderScreenWall();
        return;
      }
      if (msg.sessionId && sessionId && msg.sessionId !== sessionId) return;
      sessionId = "";
      setControls(false);
      if (screenOpen) activeMeta.textContent = `控制已停止：${msg.reason || "ended"}`;
    }
    if (msg.type === "input-result" && msg.ok === false) {
      const targetDeviceId = String(msg.deviceId || "").toUpperCase();
      if (targetDeviceId === activeDeviceId && screenOpen) {
        activeMeta.textContent = `输入执行失败：${msg.error || "dispatch_failed"}`;
      }
      if (targetDeviceId === wallActiveDeviceId && isWallControlOpen()) {
        wallControlMeta.textContent = `输入执行失败：${msg.error || "dispatch_failed"}`;
      }
    }
    if (msg.type === "error") {
      const errorDeviceId = String(msg.device?.id || msg.deviceId || "").toUpperCase();
      if (msg.error === "rtc_not_supported" && errorDeviceId && errorDeviceId === activeDeviceId) {
        fallbackToRelay("rtc_not_supported");
        return;
      }
      if (errorDeviceId && pendingControlIntents.get(errorDeviceId) === "monitor") {
        pendingControlIntents.delete(errorDeviceId);
        renderScreenWall();
        updateWallControlState();
        return;
      }
      if (screenOpen) activeMeta.textContent = errorText(msg);
      if (msg.error === "bad_session") {
        const badSessionId = String(msg.sessionId || "");
        const badMonitorDeviceId = deviceIdByMonitorSession(badSessionId);
        if (badMonitorDeviceId) {
          monitorSessions.delete(badMonitorDeviceId);
          setWallInputReady(false);
          syncMonitorSessions();
          updateWallControlState();
          return;
        }
        if (!badSessionId || badSessionId === sessionId) {
          sessionId = "";
          setInputReady(false);
          resumeActiveControl();
        }
      }
    }
  });
}

function openScreen() {
  screenOpen = true;
  viewerWrap.classList.remove("closed");
  screenPlaceholder.classList.add("hidden");
  fullscreenBtn.textContent = isViewerFullscreen() ? "恢复" : "全屏";
}

function closeScreen(stopRemote = true) {
  const closingDeviceId = activeDeviceId;
  const closingRtcSessionId = rtcState?.sessionId || "";
  const closingRelaySessionId = closingRtcSessionId === sessionId ? "" : sessionId;
  closeRtc("screen_closed", stopRemote);
  if (isViewerFullscreen()) {
    document.exitFullscreen().catch(() => {});
  }
  fullscreenBtn.textContent = "全屏";
  if (stopRemote && closingRelaySessionId && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop-control", sessionId: closingRelaySessionId }));
  }
  if (closingDeviceId) pendingControlIntents.delete(closingDeviceId);
  screenOpen = false;
  sessionId = "";
  activeDeviceId = "";
  frameSize = { width: 0, height: 0 };
  frameDrawSerial += 1;
  lastFrameTimestamp = 0;
  setInputReady(false);
  stopBtn.disabled = true;
  viewer.classList.remove("has-frame");
  viewer.classList.remove("rtc-active");
  rtcVideo.classList.add("hidden");
  rtcVideo.srcObject = null;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  viewerWrap.classList.add("closed");
  screenPlaceholder.classList.remove("hidden");
  activeTitle.textContent = "未打开屏幕";
  activeMeta.textContent = "点击下方设备入口，打开对应设备屏幕。";
  renderDevices();
  if (closingDeviceId && shouldKeepMonitoring(closingDeviceId)) {
    syncMonitorSessions();
    renderScreenWall();
  }
}

function resumeActiveControl() {
  if (!screenOpen || !activeDeviceId || sessionId || isControlStarting(activeDeviceId) || !ws || ws.readyState !== WebSocket.OPEN) return;
  const device = devices.find((item) => item.id === activeDeviceId);
  if (!device?.online || !hasScreenPermission(device)) return;
  activeMeta.textContent = "连接恢复，正在重新进入设备";
  requestControlSession(device);
}

function renderDevices() {
  if (!currentUser || currentUser.status !== "approved") return;
  renderFileTargets();
  if (!devices.length) {
    deviceShelf.innerHTML = `<div class="placeholder-card">还没有添加设备，请输入设备 ID 和验证码匹配。</div>`;
    return;
  }
  deviceShelf.innerHTML = "";
  for (const device of devices) {
    const desktop = isDesktopDevice(device);
    const card = document.createElement("div");
    card.className = `phone-card ${desktop ? "desktop-card" : ""} ${device.id === activeDeviceId ? "active" : ""} ${device.online ? "" : "offline"}`;
    card.innerHTML = `
      <span class="phone-delete" title="删除设备" data-binding-id="${device.bindingId || ""}">×</span>
      <span class="phone-top"></span>
      <strong>${escapeHtml(device.id)}</strong>
      <input class="device-label-input" value="${escapeAttr(deviceName(device))}" aria-label="设备标记">
      <small class="device-status ${device.online ? "online" : "offline"}">${device.online ? "在线" : "离线"}</small>
      <div class="badges">
        <i class="badge agent-badge">${escapeHtml(device.agentVersion ? `Agent ${device.agentVersion}` : "Agent -")}</i>
        <i class="badge ${hasScreenPermission(device) ? "" : "warn"}">屏幕${hasScreenPermission(device) ? "已开" : "未开"}</i>
        <i class="badge ${canControlDevice(device) ? "" : "warn"}">控制${canControlDevice(device) ? "可用" : "未就绪"}</i>
      </div>
      <label class="monitor-toggle">
        <input type="checkbox" ${device.monitorAlways ? "checked" : ""}>
        <span>时刻监控</span>
      </label>
    `;
    card.addEventListener("click", () => startControl(device.id));
    const deleteBtn = card.querySelector(".phone-delete");
    deleteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteBinding(device);
    });
    const labelInput = card.querySelector(".device-label-input");
    labelInput.addEventListener("click", (event) => event.stopPropagation());
    labelInput.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") labelInput.blur();
    });
    labelInput.addEventListener("blur", () => updateBinding(device, { label: labelInput.value.trim() }));

    const monitorToggle = card.querySelector(".monitor-toggle");
    monitorToggle.addEventListener("click", (event) => event.stopPropagation());
    const monitorInput = card.querySelector(".monitor-toggle input");
    monitorInput.addEventListener("click", (event) => event.stopPropagation());
    monitorInput.addEventListener("change", () => updateBinding(device, { monitorAlways: monitorInput.checked }));
    deviceShelf.appendChild(card);
  }
}

function shouldKeepMonitoring(deviceId) {
  if (!deviceId) return false;
  const device = devices.find((item) => item.id === deviceId);
  return Boolean(device?.monitorAlways);
}

function monitoredDevices() {
  return devices.filter((device) => device.monitorAlways);
}

function canMonitorDevice(device) {
  return Boolean(device?.online && hasScreenPermission(device) && !supportsRtc(device));
}

function deviceIdByMonitorSession(targetSessionId) {
  if (!targetSessionId) return "";
  for (const [deviceId, monitorSessionId] of monitorSessions) {
    if (monitorSessionId === targetSessionId) return deviceId;
  }
  return "";
}

function clearMonitorSessions(notifyRemote = true, clearAllIntents = false) {
  if (notifyRemote && ws?.readyState === WebSocket.OPEN) {
    for (const monitorSessionId of monitorSessions.values()) {
      ws.send(JSON.stringify({ type: "stop-control", sessionId: monitorSessionId }));
    }
  }
  monitorSessions.clear();
  if (clearAllIntents) {
    pendingControlIntents.clear();
  } else {
    for (const [deviceId, intent] of Array.from(pendingControlIntents)) {
      if (intent === "monitor") pendingControlIntents.delete(deviceId);
    }
  }
  previewFrameSerials.clear();
  previewFrameTimestamps.clear();
  wallFrameDrawSerial += 1;
  wallFrameSize = { width: 0, height: 0 };
  wallLastFrameTimestamp = 0;
}

function stopMonitorSession(deviceId) {
  const monitorSessionId = monitorSessions.get(deviceId);
  if (monitorSessionId && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop-control", sessionId: monitorSessionId }));
  }
  monitorSessions.delete(deviceId);
  pendingControlIntents.delete(deviceId);
  previewFrameSerials.delete(deviceId);
  previewFrameTimestamps.delete(deviceId);
  if (wallActiveDeviceId === deviceId) updateWallControlState();
}

function syncMonitorSessions() {
  if (activeStageTab !== "wall") {
    clearMonitorSessions(true);
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const wanted = new Set(monitoredDevices().filter(canMonitorDevice).map((device) => device.id));
  if (sessionId && activeDeviceId) {
    wanted.delete(activeDeviceId);
  }

  for (const [deviceId, monitorSessionId] of Array.from(monitorSessions)) {
    if (wanted.has(deviceId)) continue;
    ws.send(JSON.stringify({ type: "stop-control", sessionId: monitorSessionId }));
    monitorSessions.delete(deviceId);
    previewFrameSerials.delete(deviceId);
    previewFrameTimestamps.delete(deviceId);
    if (wallActiveDeviceId === deviceId) updateWallControlState();
  }

  for (const deviceId of Array.from(pendingControlIntents.keys())) {
    if (pendingControlIntents.get(deviceId) === "monitor" && !wanted.has(deviceId)) {
      pendingControlIntents.delete(deviceId);
    }
  }

  for (const device of devices) {
    if (!wanted.has(device.id)) continue;
    if (monitorSessions.has(device.id) || pendingControlIntents.get(device.id) === "monitor") continue;
    pendingControlIntents.set(device.id, "monitor");
    ws.send(JSON.stringify({ type: "control", deviceId: device.id, mode: "monitor" }));
  }
}

function renderScreenWall() {
  if (!screenWallGrid) return;
  const wallDevices = monitoredDevices();
  const nextKey = wallDevices.map((device) => `${device.id}:${device.online ? 1 : 0}:${hasScreenPermission(device) ? 1 : 0}:${monitorSessions.has(device.id) ? 1 : 0}`).join("|");
  screenWallEmpty.classList.toggle("hidden", wallDevices.length > 0);
  if (nextKey === wallRenderKey) {
    applyWallTileSizes();
    syncMonitorSessions();
    updateWallControlState();
    return;
  }
  wallRenderKey = nextKey;
  screenWallGrid.innerHTML = "";
  for (const device of wallDevices) {
    const online = canMonitorDevice(device);
    const connected = monitorSessions.has(device.id);
    const size = wallTileSize(device.id);
    const tile = document.createElement("article");
    tile.className = `wall-tile ${online ? "" : "offline"}`;
    tile.dataset.wallTile = device.id;
    tile.style.width = `${size.width}px`;
    tile.style.height = `${size.height}px`;
    tile.innerHTML = `
      <header>
        <div>
          <strong>${escapeHtml(deviceName(device))}</strong>
          <span>${escapeHtml(device.id)} · ${online ? (connected ? "监控中" : "连接中") : (device.online ? "屏幕未开" : "离线")}</span>
        </div>
        <button class="wall-fullscreen-icon" type="button" data-fullscreen-device="${escapeAttr(device.id)}" title="全屏播放" aria-label="全屏播放">⛶</button>
      </header>
      <div class="wall-screen">
        <canvas data-wall-device="${escapeAttr(device.id)}"></canvas>
        <div class="wall-screen-empty">${online ? "等待画面" : "不可监控"}</div>
      </div>
      <span class="wall-tile-resize wall-tile-resize-nw" data-wall-tile-resize="nw" aria-hidden="true"></span>
      <span class="wall-tile-resize wall-tile-resize-ne" data-wall-tile-resize="ne" aria-hidden="true"></span>
      <span class="wall-tile-resize wall-tile-resize-sw" data-wall-tile-resize="sw" aria-hidden="true"></span>
      <span class="wall-tile-resize wall-tile-resize-se" data-wall-tile-resize="se" aria-hidden="true"></span>
    `;
    tile.querySelector(".wall-screen").addEventListener("dblclick", () => openWallControl(device.id));
    tile.querySelector("[data-fullscreen-device]").addEventListener("click", (event) => {
      event.stopPropagation();
      toggleWallTileFullscreen(tile);
    });
    for (const handle of tile.querySelectorAll("[data-wall-tile-resize]")) {
      handle.addEventListener("pointerdown", beginWallTileResize);
    }
    screenWallGrid.appendChild(tile);
  }
  syncMonitorSessions();
  updateWallControlState();
}

function wallTileSize(deviceId) {
  return wallTileSizes.get(deviceId) || { width: 280, height: 540 };
}

function applyWallTileSizes() {
  for (const tile of screenWallGrid.querySelectorAll("[data-wall-tile]")) {
    const size = wallTileSize(tile.dataset.wallTile);
    tile.style.width = `${size.width}px`;
    tile.style.height = `${size.height}px`;
  }
}

function activeWallSessionId() {
  if (!wallActiveDeviceId) return "";
  return monitorSessions.get(wallActiveDeviceId) || (screenOpen && activeDeviceId === wallActiveDeviceId ? sessionId : "");
}

function setWallInputReady(enabled) {
  wallInputReady = Boolean(enabled);
  wallControlBackBtn.disabled = !wallInputReady;
  wallControlHomeBtn.disabled = !wallInputReady;
  wallControlHomeSwipeBtn.disabled = !wallInputReady;
}

function isWallControlOpen() {
  return Boolean(wallActiveDeviceId && !wallControlWindow.classList.contains("hidden"));
}

function updateWallControlState() {
  if (!isWallControlOpen()) return;
  const device = devices.find((item) => item.id === wallActiveDeviceId);
  if (!device) {
    closeWallControl();
    return;
  }
  const hasSession = Boolean(activeWallSessionId());
  const canView = canMonitorDevice(device) || (screenOpen && activeDeviceId === device.id);
  setWallInputReady(Boolean(hasSession && canControlDevice(device)));
  wallControlTitle.textContent = deviceName(device);
  if (!device.online) {
    wallControlMeta.textContent = `${device.id} · 离线`;
    return;
  }
  if (!hasScreenPermission(device)) {
    wallControlMeta.textContent = `${device.id} · 屏幕未开`;
    return;
  }
  if (!canView) {
    wallControlMeta.textContent = `${device.id} · 不可监控`;
    return;
  }
  wallControlMeta.textContent = `${device.id} · ${hasSession ? (wallInputReady ? "可操作" : "仅观看") : "连接中"}`;
}

function copyWallPreviewToControl(deviceId) {
  const previewCanvas = screenWallGrid.querySelector(`canvas[data-wall-device="${cssEscape(deviceId)}"]`);
  if (!previewCanvas || !previewCanvas.width || !previewCanvas.height) return false;
  wallControlCanvas.width = previewCanvas.width;
  wallControlCanvas.height = previewCanvas.height;
  wallFrameSize = { width: previewCanvas.width, height: previewCanvas.height };
  wallControlCtx.drawImage(previewCanvas, 0, 0);
  wallControlViewer.classList.add("has-frame");
  wallLastFrameTimestamp = Number(previewFrameTimestamps.get(deviceId) || Date.now());
  return true;
}

function openWallControl(deviceId) {
  wallActiveDeviceId = String(deviceId || "").trim().toUpperCase();
  const device = devices.find((item) => item.id === wallActiveDeviceId);
  wallControlWindow.classList.remove("hidden");
  normalizeWallControlBounds();
  wallControlViewer.classList.remove("has-frame");
  wallFrameSize = { width: 0, height: 0 };
  wallFrameDrawSerial += 1;
  wallLastFrameTimestamp = 0;
  wallControlCtx.clearRect(0, 0, wallControlCanvas.width, wallControlCanvas.height);
  wallControlTitle.textContent = device ? deviceName(device) : wallActiveDeviceId;
  wallControlMeta.textContent = "正在准备画面";
  copyWallPreviewToControl(wallActiveDeviceId);
  updateWallControlState();
  syncMonitorSessions();
  wallControlCanvas.focus({ preventScroll: true });
}

function closeWallControl(resetActive = true) {
  if (document.fullscreenElement === wallControlWindow) {
    document.exitFullscreen().catch(() => {});
  }
  clearTimeout(wallPendingTextTimer);
  wallPendingText = "";
  wallDragStart = null;
  wallDragLast = null;
  wallDragMoved = false;
  wallDragStartedRemote = false;
  wallDragButton = "left";
  wallResizeState = null;
  setWallInputReady(false);
  wallControlWindow.classList.add("hidden");
  wallControlViewer.classList.remove("has-frame");
  if (resetActive) wallActiveDeviceId = "";
}

async function toggleWallControlFullscreen() {
  if (!isWallControlOpen()) return;
  if (document.fullscreenElement === wallControlWindow) {
    await document.exitFullscreen().catch(() => {});
  } else {
    await wallControlWindow.requestFullscreen().catch(() => {});
  }
  wallControlFullscreenBtn.textContent = document.fullscreenElement === wallControlWindow ? "恢复" : "全屏";
  wallControlCanvas.focus({ preventScroll: true });
}

async function toggleWallTileFullscreen(tile) {
  if (document.fullscreenElement === tile) {
    await document.exitFullscreen().catch(() => {});
  } else {
    await tile.requestFullscreen().catch(() => {});
  }
}

function wallTileMinSize() {
  return { width: 180, height: 300 };
}

function clampWallTileSize(size) {
  const min = wallTileMinSize();
  return {
    width: Math.max(min.width, Math.min(900, Math.round(size.width))),
    height: Math.max(min.height, Math.min(1200, Math.round(size.height)))
  };
}

function beginWallTileResize(event) {
  if (document.fullscreenElement) return;
  const handle = event.currentTarget.dataset.wallTileResize;
  const tile = event.currentTarget.closest("[data-wall-tile]");
  if (!handle || !tile) return;
  event.preventDefault();
  event.stopPropagation();
  const rect = tile.getBoundingClientRect();
  wallTileResizeState = {
    handle,
    tile,
    deviceId: tile.dataset.wallTile,
    pointerId: event.pointerId,
    target: event.currentTarget,
    startX: event.clientX,
    startY: event.clientY,
    width: rect.width,
    height: rect.height
  };
  event.currentTarget.classList.add("resizing");
  document.body.classList.add("wall-resizing");
  document.body.style.cursor = getComputedStyle(event.currentTarget).cursor;
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

function moveWallTileResize(event) {
  if (!wallTileResizeState) return;
  if (event.pointerId !== undefined && event.pointerId !== wallTileResizeState.pointerId) return;
  event.preventDefault();
  const dx = event.clientX - wallTileResizeState.startX;
  const dy = event.clientY - wallTileResizeState.startY;
  const next = clampWallTileSize({
    width: wallTileResizeState.width + (wallTileResizeState.handle.includes("w") ? -dx : dx),
    height: wallTileResizeState.height + (wallTileResizeState.handle.includes("n") ? -dy : dy)
  });
  wallTileSizes.set(wallTileResizeState.deviceId, next);
  wallTileResizeState.tile.style.width = `${next.width}px`;
  wallTileResizeState.tile.style.height = `${next.height}px`;
}

function endWallTileResize(event) {
  if (!wallTileResizeState) return;
  if (event.pointerId !== undefined && event.pointerId !== wallTileResizeState.pointerId) return;
  wallTileResizeState.target?.classList.remove("resizing");
  document.body.classList.remove("wall-resizing");
  document.body.style.cursor = "";
  wallTileResizeState = null;
}

function wallControlMinSize() {
  const styles = getComputedStyle(wallControlWindow);
  return {
    width: Number.parseFloat(styles.minWidth) || 360,
    height: Number.parseFloat(styles.minHeight) || 460
  };
}

function clampWallRect(rect) {
  const margin = 8;
  const min = wallControlMinSize();
  const maxWidth = Math.max(min.width, window.innerWidth - margin * 2);
  const maxHeight = Math.max(min.height, window.innerHeight - margin * 2);
  let width = Math.max(min.width, Math.min(maxWidth, rect.width));
  let height = Math.max(min.height, Math.min(maxHeight, rect.height));
  let left = rect.left;
  let top = rect.top;
  left = Math.max(margin, Math.min(window.innerWidth - margin - width, left));
  top = Math.max(margin, Math.min(window.innerHeight - margin - height, top));
  return { left, top, width, height };
}

function setWallControlRect(rect) {
  const next = clampWallRect(rect);
  wallControlWindow.style.left = `${Math.round(next.left)}px`;
  wallControlWindow.style.top = `${Math.round(next.top)}px`;
  wallControlWindow.style.right = "auto";
  wallControlWindow.style.bottom = "auto";
  wallControlWindow.style.width = `${Math.round(next.width)}px`;
  wallControlWindow.style.height = `${Math.round(next.height)}px`;
}

function normalizeWallControlBounds() {
  if (document.fullscreenElement === wallControlWindow) return;
  const rect = wallControlWindow.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  setWallControlRect(rect);
}

function beginWallResize(event) {
  if (document.fullscreenElement === wallControlWindow) return;
  const handle = event.currentTarget.dataset.wallResize;
  if (!handle) return;
  event.preventDefault();
  event.stopPropagation();
  normalizeWallControlBounds();
  const rect = wallControlWindow.getBoundingClientRect();
  wallResizeState = {
    handle,
    pointerId: event.pointerId,
    target: event.currentTarget,
    startX: event.clientX,
    startY: event.clientY,
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height
  };
  event.currentTarget.classList.add("resizing");
  document.body.classList.add("wall-resizing");
  document.body.style.cursor = getComputedStyle(event.currentTarget).cursor;
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

function moveWallResize(event) {
  if (!wallResizeState) return;
  if (event.pointerId !== undefined && event.pointerId !== wallResizeState.pointerId) return;
  event.preventDefault();
  const dx = event.clientX - wallResizeState.startX;
  const dy = event.clientY - wallResizeState.startY;
  const min = wallControlMinSize();
  const right = wallResizeState.left + wallResizeState.width;
  const bottom = wallResizeState.top + wallResizeState.height;
  const rect = {
    left: wallResizeState.left,
    top: wallResizeState.top,
    width: wallResizeState.width,
    height: wallResizeState.height
  };
  if (wallResizeState.handle.includes("e")) {
    rect.width = wallResizeState.width + dx;
  }
  if (wallResizeState.handle.includes("s")) {
    rect.height = wallResizeState.height + dy;
  }
  if (wallResizeState.handle.includes("w")) {
    rect.left = wallResizeState.left + dx;
    rect.width = wallResizeState.width - dx;
    if (rect.width < min.width) {
      rect.width = min.width;
      rect.left = right - min.width;
    }
  }
  if (wallResizeState.handle.includes("n")) {
    rect.top = wallResizeState.top + dy;
    rect.height = wallResizeState.height - dy;
    if (rect.height < min.height) {
      rect.height = min.height;
      rect.top = bottom - min.height;
    }
  }
  setWallControlRect(rect);
}

function endWallResize(event) {
  if (!wallResizeState) return;
  if (event.pointerId !== undefined && event.pointerId !== wallResizeState.pointerId) return;
  wallResizeState.target?.classList.remove("resizing");
  document.body.classList.remove("wall-resizing");
  document.body.style.cursor = "";
  wallResizeState = null;
}

async function updateBinding(device, patch) {
  if (!device.bindingId) return;
  try {
    const data = await api(`/api/bindings/${encodeURIComponent(device.bindingId)}`, {
      method: "PATCH",
      body: patch
    });
    devices = data.devices || devices;
    renderDevices();
    syncMonitorSessions();
    renderScreenWall();
    if (activeDeviceId) syncActiveDeviceState();
  } catch (error) {
    bindHint.textContent = errorText(error.data || { error: error.message });
    renderDevices();
  }
}

async function deleteBinding(device) {
  if (!device.bindingId) return;
  if (!confirm(`删除设备 ${device.id}？`)) return;
  try {
    const data = await api(`/api/bindings/${encodeURIComponent(device.bindingId)}`, { method: "DELETE" });
    devices = data.devices || [];
    stopMonitorSession(device.id);
    if (activeDeviceId === device.id) {
      closeScreen(false);
    }
    renderDevices();
  } catch (error) {
    bindHint.textContent = errorText(error.data || { error: error.message });
  }
}

function renderFileTargets() {
  if (!fileDeviceSelect) return;
  const selected = fileDeviceSelect.value;
  const onlineDevices = devices.filter((device) => device.online);
  fileDeviceSelect.innerHTML = "";
  if (!onlineDevices.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有在线设备";
    fileDeviceSelect.appendChild(option);
    if (fileSubmitBtn) fileSubmitBtn.disabled = true;
    return;
  }
  for (const device of onlineDevices) {
    const option = document.createElement("option");
    option.value = device.id;
    option.textContent = `${device.id} · ${deviceName(device)} · ${platformText(device)}`;
    fileDeviceSelect.appendChild(option);
  }
  if (selected && onlineDevices.some((device) => device.id === selected)) {
    fileDeviceSelect.value = selected;
  } else if (activeDeviceId && onlineDevices.some((device) => device.id === activeDeviceId)) {
    fileDeviceSelect.value = activeDeviceId;
  }
  if (fileSubmitBtn) fileSubmitBtn.disabled = false;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function fileStatusText(status) {
  if (status === "queued") return "已排队";
  if (status === "dispatched") return "已派发";
  if (status === "downloading") return "设备下载中";
  if (status === "saved") return "已保存";
  if (status === "failed") return "失败";
  return status || "-";
}

function fileStatusClass(status) {
  if (status === "saved") return "saved";
  if (status === "failed") return "failed";
  if (status === "downloading") return "downloading";
  return "pending";
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function fileTransferDeviceLabel(transfer) {
  const deviceId = String(transfer?.deviceId || "").toUpperCase();
  const device = devices.find((item) => item.id === deviceId);
  if (!device) return deviceId || "-";
  return `${deviceId} · ${deviceName(device)}`;
}

function upsertFileTransfer(transfer) {
  if (!transfer?.id) return;
  fileTransfers = [transfer, ...fileTransfers.filter((item) => item.id !== transfer.id)]
    .sort((a, b) => String(b.updatedAt || b.createdAt).localeCompare(String(a.updatedAt || a.createdAt)))
    .slice(0, 100);
  renderFileTransfers();
  if (fileHint) {
    fileHint.textContent = `${transfer.fileName || "文件"}：${fileStatusText(transfer.status)}${transfer.error ? `，${transfer.error}` : ""}`;
  }
}

function renderFileTransfers() {
  if (!fileTransferLog) return;
  fileTransferLog.innerHTML = "";
  for (const transfer of fileTransfers) {
    const row = document.createElement("div");
    row.className = `file-transfer-row ${transfer.status || ""}`;
    const title = document.createElement("strong");
    title.textContent = transfer.fileName || "file";
    const meta = document.createElement("span");
    const savedText = transfer.devicePath ? ` · ${transfer.devicePath}` : "";
    const errorTextValue = transfer.error ? ` · ${transfer.error}` : "";
    meta.textContent = `${transfer.deviceId || "-"} · ${fileStatusText(transfer.status)} · ${formatBytes(transfer.size)}${savedText}${errorTextValue}`;
    row.appendChild(title);
    row.appendChild(meta);
    fileTransferLog.appendChild(row);
  }
  renderFileTransferWorkspace();
}

function renderFileTransferWorkspace() {
  if (!fileTransferTable || !fileTransferEmpty) return;
  fileTransferTable.innerHTML = "";
  fileTransferEmpty.classList.toggle("hidden", fileTransfers.length > 0);
  fileTransferTable.classList.toggle("hidden", fileTransfers.length === 0);
  const savedCount = fileTransfers.filter((item) => item.status === "saved").length;
  const failedCount = fileTransfers.filter((item) => item.status === "failed").length;
  if (fileTransferSummary) {
    fileTransferSummary.textContent = fileTransfers.length
      ? `最近 ${fileTransfers.length} 条 · 已保存 ${savedCount} · 失败 ${failedCount}`
      : "暂无传输记录";
  }
  for (const transfer of fileTransfers) {
    const row = document.createElement("article");
    row.className = `file-transfer-card ${fileStatusClass(transfer.status)}`;

    const head = document.createElement("div");
    head.className = "file-transfer-card-head";
    const title = document.createElement("strong");
    title.textContent = transfer.fileName || "file";
    const status = document.createElement("span");
    status.className = "file-transfer-status";
    status.textContent = fileStatusText(transfer.status);
    head.appendChild(title);
    head.appendChild(status);

    const grid = document.createElement("div");
    grid.className = "file-transfer-fields";
    const fields = [
      ["目标设备", fileTransferDeviceLabel(transfer)],
      ["大小", formatBytes(transfer.size)],
      ["更新时间", formatDateTime(transfer.updatedAt || transfer.createdAt)],
      ["保存位置", transfer.devicePath || (transfer.status === "saved" ? "设备未返回路径" : "-")]
    ];
    if (transfer.error) fields.push(["错误", transfer.error]);
    for (const [labelText, valueText] of fields) {
      const label = document.createElement("span");
      label.textContent = labelText;
      const value = document.createElement("b");
      value.textContent = valueText || "-";
      grid.appendChild(label);
      grid.appendChild(value);
    }

    row.appendChild(head);
    row.appendChild(grid);
    fileTransferTable.appendChild(row);
  }
}

async function loadFileTransfers() {
  if (!sessionToken || currentUser?.status !== "approved") return;
  try {
    const data = await api("/api/file-transfers");
    fileTransfers = (data.transfers || [])
      .sort((a, b) => String(b.updatedAt || b.createdAt).localeCompare(String(a.updatedAt || a.createdAt)))
      .slice(0, 100);
    renderFileTransfers();
  } catch (error) {
    if (fileTransferSummary) {
      fileTransferSummary.textContent = errorText(error.data || { error: error.message });
    }
  }
}

function setActiveDevice(device) {
  activeTitle.textContent = device.id;
  activeMeta.textContent = `${deviceName(device)} · ${device.model || device.osVersion || "未知型号"}`;
  viewer.classList.toggle("desktop-viewer", isDesktopDevice(device));
  viewer.classList.toggle("phone-viewer", !isDesktopDevice(device));
  renderDevices();
}

function syncActiveDeviceState() {
  if (!activeDeviceId) return;
  const device = devices.find((item) => item.id === activeDeviceId);
  if (!device) return;
  if (!device.online) {
    sessionId = "";
    setInputReady(false);
    if (screenOpen) activeMeta.textContent = "设备已添加，但当前离线";
    return;
  }
  setInputReady(Boolean(sessionId) && canControlDevice(device));
  stopBtn.disabled = !sessionId;
  if (sessionId && canControlDevice(device)) {
    activeMeta.textContent = `${deviceName(device)} · ${device.model || device.osVersion || "未知型号"} · 可操作`;
  } else if (sessionId) {
    activeMeta.textContent = `已进入画面，仅可观看：${controlBlockReason(device) || "控制未就绪"}`;
  }
}

function setControls(enabled) {
  setInputReady(enabled);
  stopBtn.disabled = !enabled;
}

function setInputReady(enabled) {
  inputReady = Boolean(enabled);
  backBtn.disabled = !inputReady;
  homeBtn.disabled = !inputReady;
  homeSwipeBtn.disabled = !inputReady;
}

function setStageTab(tab) {
  const previousTab = activeStageTab;
  activeStageTab = ["wall", "files"].includes(tab) ? tab : "control";
  controlTabBtn.classList.toggle("active", activeStageTab === "control");
  wallTabBtn.classList.toggle("active", activeStageTab === "wall");
  filesTabBtn.classList.toggle("active", activeStageTab === "files");
  stageBody.classList.toggle("wall-mode", activeStageTab === "wall");
  stageBody.classList.toggle("files-mode", activeStageTab === "files");
  screenWorkspace.classList.toggle("hidden", activeStageTab !== "control");
  screenWall.classList.toggle("hidden", activeStageTab !== "wall");
  fileTransferWorkspace.classList.toggle("hidden", activeStageTab !== "files");
  if (activeStageTab === "wall") {
    if (activeDeviceId) pendingControlIntents.delete(activeDeviceId);
    if (screenOpen) closeScreen(true);
    wallRenderKey = "";
    renderScreenWall();
  } else if (activeStageTab === "files") {
    if (screenOpen) closeScreen(true);
    closeWallControl();
    clearMonitorSessions(true);
    wallRenderKey = "";
    renderFileTransferWorkspace();
  } else if (previousTab === "wall") {
    closeWallControl();
    clearMonitorSessions(true);
    wallRenderKey = "";
  } else if (previousTab === "files") {
    renderFileTransferWorkspace();
  }
}

function errorText(msg) {
  const device = msg.device || {};
  if (msg.error === "screen_not_ready") return `设备已在线，但 ${platformText(device)} 端还没有开启屏幕权限`;
  if (msg.error === "input_not_ready") return `当前仅可观看：${controlBlockReason(device) || "控制未就绪"}`;
  if (msg.error === "device_offline") return "设备不在线";
  if (msg.error === "device_not_bound") return "设备还没有绑定到当前账号";
  if (msg.error === "rtc_not_supported") return "设备暂不支持直连，正在使用中转";
  if (msg.error === "rtc_required") return "该设备优先使用 WebRTC，直连失败后才会进入限时中转";
  if (msg.error === "bad_rtc_session") return "直连会话已失效";
  if (msg.error === "bad_verification_code") return "设备验证码不正确";
  if (msg.error === "user_not_approved") return "账号还没有审核通过";
  if (msg.error === "invalid_login") return "用户名或密码错误";
  if (msg.error === "username_exists") return "用户名已存在";
  if (msg.error === "bad_credentials") return "用户名或密码格式不正确";
  if (msg.error === "binding_not_found") return "设备绑定记录不存在";
  if (msg.error === "empty_file") return "请选择要下发的文件";
  if (msg.error === "file_too_large") return "文件超过服务端限制";
  if (msg.error === "transfer_not_found") return "文件下发任务不存在或已过期";
  return `操作失败：${msg.error}`;
}

function supportsRtc(device) {
  const capabilities = device?.rtcCapabilities || {};
  return Boolean(window.RTCPeerConnection && capabilities.webrtc && capabilities.video);
}

function rtcFirstFrameTimeoutMs(device) {
  return devicePlatform(device) === "android" ? 15000 : 8000;
}

function isControlStarting(deviceId = activeDeviceId) {
  const intent = deviceId ? pendingControlIntents.get(deviceId) : "";
  return intent === "control";
}

function requestControlSession(device, options = {}) {
  if (!device?.id || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (sessionId && activeDeviceId === device.id) return;
  if (isControlStarting(device.id)) return;
  pendingControlIntents.set(device.id, "control");
  if (supportsRtc(device)) {
    ws.send(JSON.stringify({
      type: "rtc-start",
      deviceId: device.id,
      mode: "control",
      quality: currentQualityProfile()
    }));
  } else {
    ws.send(JSON.stringify({
      type: "control",
      deviceId: device.id,
      mode: "control",
      relayFallback: Boolean(options.relayFallback),
      reason: options.reason || ""
    }));
  }
}

function closeRtc(reason = "closed", notify = true) {
  const state = rtcState;
  rtcState = null;
  clearTimeout(state?.fallbackTimer);
  clearTimeout(state?.controlTimer);
  clearInterval(state?.statsTimer);
  try {
    state?.controlChannel?.close();
  } catch {
    // Best effort close.
  }
  try {
    state?.pc?.close();
  } catch {
    // Best effort close.
  }
  if (rtcVideo) {
    rtcVideo.pause();
    rtcVideo.srcObject = null;
    rtcVideo.classList.add("hidden");
  }
  viewer.classList.remove("rtc-active", "rtc-video-active");
  if (notify && state?.sessionId && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "rtc-stop", sessionId: state.sessionId, deviceId: state.deviceId, reason }));
  }
}

function fallbackToRelay(reason = "fallback") {
  const deviceId = rtcState?.deviceId || activeDeviceId;
  closeRtc(reason, true);
  if (!deviceId || !ws || ws.readyState !== WebSocket.OPEN) return;
  pendingControlIntents.delete(deviceId);
  activeMeta.textContent = `WebRTC 直连失败（${reason}），未启用 JPEG 中转`;
  sessionId = "";
}

async function handleRtcReady(msg) {
  if (msg.deviceId !== activeDeviceId || !screenOpen) return;
  if (!window.RTCPeerConnection) {
    sessionId = "";
    pendingControlIntents.delete(msg.deviceId);
    activeMeta.textContent = "当前浏览器不支持 WebRTC";
    return;
  }
  closeRtc("replaced", false);
  sessionId = msg.sessionId;
  pendingControlIntents.delete(msg.deviceId);
  const device = msg.device || devices.find((item) => item.id === activeDeviceId);
  if (device) {
    setActiveDevice(device);
    setInputReady(canControlDevice(device));
  }
  const pc = new RTCPeerConnection({ iceServers: msg.iceServers || [] });
  const controlChannel = pc.createDataChannel("control", { ordered: true });
  const wantsVideoTrack = Boolean(device?.rtcCapabilities?.video);
  if (!wantsVideoTrack) {
    closeRtc("video_track_required", false);
    activeMeta.textContent = "设备未提供 WebRTC 视频轨道";
    return;
  }
  pc.addTransceiver("video", { direction: "recvonly" });
  rtcState = {
    pc,
    controlChannel,
    sessionId: msg.sessionId,
    deviceId: msg.deviceId,
    connected: false,
    lastStatsAt: 0,
    lastBytesReceived: 0,
    lastBytesSent: 0,
    statsTimer: 0,
    fallbackTimer: setTimeout(() => fallbackToRelay("rtc_timeout"), rtcFirstFrameTimeoutMs(device)),
    controlTimer: setTimeout(() => {
      if (rtcState?.sessionId === msg.sessionId && rtcState.controlChannel?.readyState !== "open") {
        fallbackToRelay("rtc_control_timeout");
      }
    }, 10000)
  };
  controlChannel.addEventListener("open", () => {
    if (rtcState?.sessionId !== msg.sessionId) return;
    clearTimeout(rtcState.controlTimer);
    activeMeta.textContent = "直连控制通道已建立";
  });
  controlChannel.addEventListener("close", () => {
    if (rtcState?.sessionId === msg.sessionId && rtcState.connected) fallbackToRelay("rtc_control_closed");
  });
  controlChannel.addEventListener("error", () => {
    if (rtcState?.sessionId === msg.sessionId) fallbackToRelay("rtc_control_error");
  });
  controlChannel.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(String(event.data || "{}"));
    } catch {
      return;
    }
    if (payload.type === "input-result" && payload.ok === false) {
      activeMeta.textContent = `输入执行失败：${payload.error || "dispatch_failed"}`;
    }
  });
  if (wantsVideoTrack) {
    pc.addEventListener("track", (event) => {
      if (rtcState?.sessionId !== msg.sessionId) return;
      const stream = event.streams[0] || new MediaStream([event.track]);
      rtcVideo.srcObject = stream;
      rtcVideo.classList.remove("hidden");
      viewer.classList.add("rtc-active", "rtc-video-active", "has-frame");
      rtcVideo.play().catch(() => {});
    });
  }
  pc.addEventListener("icecandidate", (event) => {
    if (!event.candidate || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({
      type: "rtc-ice-candidate",
      sessionId: msg.sessionId,
      deviceId: msg.deviceId,
      candidate: event.candidate.toJSON()
    }));
  });
  pc.addEventListener("connectionstatechange", () => {
    if (rtcState?.sessionId !== msg.sessionId) return;
    const state = pc.connectionState;
    reportRtcState(state);
    if (state === "connected") {
      startRtcStats();
      rtcState.connected = true;
      activeMeta.textContent = "WebRTC 传输已连接，等待视频轨道";
    }
    if (state === "failed") fallbackToRelay("rtc_failed");
    if (state === "closed") closeRtc("rtc_closed", false);
  });
  pc.addEventListener("iceconnectionstatechange", () => {
    if (rtcState?.sessionId !== msg.sessionId) return;
    if (pc.iceConnectionState === "failed") fallbackToRelay("ice_failed");
  });
  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({
      type: "rtc-offer",
      sessionId: msg.sessionId,
      deviceId: msg.deviceId,
      sdp: offer.sdp
    }));
    activeMeta.textContent = `正在尝试局域网/P2P 直连 · ${currentQualityProfile().label}档`;
  } catch {
    fallbackToRelay("offer_failed");
  }
}

async function handleRtcAnswer(msg) {
  if (!rtcState || rtcState.sessionId !== msg.sessionId || !rtcState.pc) return;
  try {
    await rtcState.pc.setRemoteDescription({ type: "answer", sdp: msg.sdp });
  } catch {
    fallbackToRelay("answer_failed");
  }
}

async function handleRtcRemoteCandidate(msg) {
  if (!rtcState || rtcState.sessionId !== msg.sessionId || !rtcState.pc || !msg.candidate) return;
  try {
    await rtcState.pc.addIceCandidate(msg.candidate);
  } catch {
    // Invalid candidates are ignored; ICE can still continue with others.
  }
}

function handleRtcPeerState(msg) {
  if (!rtcState || rtcState.sessionId !== msg.sessionId) return;
  if (msg.selectedCandidateType) rtcState.selectedCandidateType = msg.selectedCandidateType;
  if (Number(msg.bitrateKbps)) rtcState.bitrateKbps = Number(msg.bitrateKbps);
  if (Number(msg.rttMs)) rtcState.rttMs = Number(msg.rttMs);
  if (msg.selectedCandidateType && msg.selectedCandidateType !== "unknown") {
    activeMeta.textContent = `WebRTC ${candidateTypeText(msg.selectedCandidateType)} · ${msg.state || "连接中"}`;
  }
}

function handleRtcStopped(msg) {
  if (!rtcState || rtcState.sessionId !== msg.sessionId) return;
  closeRtc(msg.reason || "peer_stopped", false);
  if (sessionId === msg.sessionId) {
    sessionId = "";
    setInputReady(false);
  }
}

function candidateTypeText(type) {
  if (type === "host") return "局域网直连";
  if (type === "srflx") return "公网 P2P";
  if (type === "relay") return "TURN 中转";
  return "连接";
}

function reportRtcState(state) {
  if (!rtcState || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    type: "rtc-state",
    sessionId: rtcState.sessionId,
    deviceId: rtcState.deviceId,
    state,
    selectedCandidateType: rtcState.selectedCandidateType || "unknown",
    rttMs: Math.round(Number(rtcState.rttMs || 0)),
    bitrateKbps: Math.round(Number(rtcState.bitrateKbps || 0)),
    bytesReceived: Math.round(Number(rtcState.bytesReceived || 0)),
    bytesSent: Math.round(Number(rtcState.bytesSent || 0)),
    packetsLost: Math.round(Number(rtcState.packetsLost || 0))
  }));
}

function startRtcStats() {
  if (!rtcState || rtcState.statsTimer) return;
  rtcState.statsTimer = setInterval(updateRtcStats, 2000);
  updateRtcStats();
}

async function updateRtcStats() {
  if (!rtcState?.pc || !ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    const stats = await rtcState.pc.getStats();
    let candidatePair = null;
    let localCandidate = null;
    let bytesReceived = 0;
    let bytesSent = 0;
    let packetsLost = 0;
    stats.forEach((report) => {
      if (report.type === "candidate-pair" && (report.selected || report.nominated || report.state === "succeeded")) {
        candidatePair = report;
      }
      if (report.type === "inbound-rtp" && report.kind === "video") {
        bytesReceived += Number(report.bytesReceived || 0);
        packetsLost += Number(report.packetsLost || 0);
      }
      if (report.type === "data-channel") {
        bytesReceived += Number(report.bytesReceived || 0);
        bytesSent += Number(report.bytesSent || 0);
      }
    });
    if (candidatePair) {
      localCandidate = stats.get(candidatePair.localCandidateId);
      rtcState.rttMs = Number(candidatePair.currentRoundTripTime || 0) * 1000;
      if (!bytesReceived) bytesReceived = Number(candidatePair.bytesReceived || 0);
      if (!bytesSent) bytesSent = Number(candidatePair.bytesSent || 0);
    }
    const nowMs = performance.now();
    const elapsed = rtcState.lastStatsAt ? Math.max(0.001, (nowMs - rtcState.lastStatsAt) / 1000) : 0;
    if (elapsed) {
      const deltaBytes = Math.max(0, bytesReceived - Number(rtcState.lastBytesReceived || 0));
      rtcState.bitrateKbps = (deltaBytes * 8) / elapsed / 1000;
    }
    rtcState.lastStatsAt = nowMs;
    rtcState.lastBytesReceived = bytesReceived;
    rtcState.lastBytesSent = bytesSent;
    rtcState.bytesReceived = bytesReceived;
    rtcState.bytesSent = bytesSent;
    rtcState.packetsLost = packetsLost;
    rtcState.selectedCandidateType = localCandidate?.candidateType || rtcState.selectedCandidateType || "unknown";
    reportRtcState(rtcState.pc.connectionState || "connected");
  } catch {
    // Stats are best effort; media/control can continue without them.
  }
}

function startControl(id) {
  const nextDeviceId = id.trim().toUpperCase();
  const previousDeviceId = activeDeviceId;
  const previousSessionId = sessionId;
  const device = devices.find((item) => item.id === nextDeviceId);
  if (previousDeviceId === nextDeviceId && (previousSessionId || isControlStarting(nextDeviceId))) {
    openScreen();
    if (device) setActiveDevice(device);
    return;
  }
  if (previousSessionId && previousDeviceId && previousDeviceId !== nextDeviceId && ws?.readyState === WebSocket.OPEN) {
    if (rtcState?.sessionId === previousSessionId) {
      closeRtc("switch_device", true);
    } else {
      ws.send(JSON.stringify({ type: "stop-control", sessionId: previousSessionId }));
    }
    sessionId = "";
    pendingControlIntents.delete(previousDeviceId);
    if (shouldKeepMonitoring(previousDeviceId)) {
      setTimeout(() => {
        syncMonitorSessions();
        renderScreenWall();
      }, 0);
    }
  }
  activeDeviceId = nextDeviceId;
  stopMonitorSession(activeDeviceId);
  openScreen();
  viewer.classList.remove("has-frame");
  frameDrawSerial += 1;
  lastFrameTimestamp = 0;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (device) setActiveDevice(device);
  if (!device?.online) {
    activeMeta.textContent = "设备已添加，但当前离线";
    return;
  }
  activeMeta.textContent = supportsRtc(device)
    ? `正在尝试局域网/P2P 直连 · ${currentQualityProfile().label}档`
    : "正在请求控制";
  requestControlSession(device);
}

function escapeAttr(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isViewerFullscreen() {
  return document.fullscreenElement === viewerWrap;
}

async function toggleViewerFullscreen() {
  if (!screenOpen || viewerWrap.classList.contains("closed")) return;
  if (isViewerFullscreen()) {
    await document.exitFullscreen().catch(() => {});
  } else {
    await viewerWrap.requestFullscreen().catch(() => {});
  }
  fullscreenBtn.textContent = isViewerFullscreen() ? "恢复" : "全屏";
  canvas.focus({ preventScroll: true });
}

function drawFrame(msg) {
  if (!msg.image) return;
  if (msg.frameKind && msg.frameKind !== "jpeg") return;
  const activeDevice = devices.find((item) => item.id === activeDeviceId);
  if (msg.deviceId === activeDeviceId && supportsRtc(activeDevice) && rtcState?.deviceId === activeDeviceId) return;
  const timestamp = Number(msg.timestamp || 0);
  if (timestamp && timestamp < lastFrameTimestamp) return;
  const drawSerial = ++frameDrawSerial;
  const img = new Image();
  img.onload = () => {
    if (drawSerial !== frameDrawSerial) return;
    if (timestamp && timestamp < lastFrameTimestamp) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    frameSize = { width: img.naturalWidth, height: img.naturalHeight };
    viewer.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
    ctx.drawImage(img, 0, 0);
    lastFrameTimestamp = timestamp || Date.now();
    viewer.classList.add("has-frame");
    if (ws && ws.readyState === WebSocket.OPEN && sessionId && msg.deviceId === activeDeviceId) {
      ws.send(JSON.stringify({ type: "frame-ack", sessionId, deviceId: msg.deviceId, frameId: msg.frameId || timestamp }));
    }
  };
  img.src = `data:image/jpeg;base64,${msg.image}`;
}

function handleTextFrame(msg) {
  if (msg.deviceId === activeDeviceId && screenOpen) {
    drawFrame(msg);
  }
  if (activeStageTab === "wall" && shouldKeepMonitoring(msg.deviceId)) {
    drawWallTextFrame(msg);
  }
  if (msg.deviceId === wallActiveDeviceId && isWallControlOpen()) {
    drawWallControlTextFrame(msg);
  }
}

function drawWallTextFrame(msg) {
  if (!msg.image || msg.frameKind && msg.frameKind !== "jpeg") return;
  const targetCanvas = screenWallGrid.querySelector(`canvas[data-wall-device="${cssEscape(msg.deviceId)}"]`);
  if (!targetCanvas) return;
  const timestamp = Number(msg.timestamp || 0);
  const lastTimestamp = Number(previewFrameTimestamps.get(msg.deviceId) || 0);
  if (timestamp && timestamp < lastTimestamp) return;
  const serial = (previewFrameSerials.get(msg.deviceId) || 0) + 1;
  previewFrameSerials.set(msg.deviceId, serial);
  const img = new Image();
  img.onload = () => {
    if (previewFrameSerials.get(msg.deviceId) !== serial) return;
    targetCanvas.width = img.naturalWidth;
    targetCanvas.height = img.naturalHeight;
    targetCanvas.getContext("2d").drawImage(img, 0, 0);
    previewFrameTimestamps.set(msg.deviceId, timestamp || Date.now());
    targetCanvas.closest(".wall-screen")?.classList.add("has-frame");
  };
  img.src = `data:image/jpeg;base64,${msg.image}`;
}

function drawWallControlTextFrame(msg) {
  if (!msg.image || msg.frameKind && msg.frameKind !== "jpeg") return;
  const timestamp = Number(msg.timestamp || 0);
  if (timestamp && timestamp < wallLastFrameTimestamp) return;
  const serial = ++wallFrameDrawSerial;
  const img = new Image();
  img.onload = () => {
    if (serial !== wallFrameDrawSerial) return;
    if (timestamp && timestamp < wallLastFrameTimestamp) return;
    wallControlCanvas.width = img.naturalWidth;
    wallControlCanvas.height = img.naturalHeight;
    wallFrameSize = { width: img.naturalWidth, height: img.naturalHeight };
    wallControlCtx.drawImage(img, 0, 0);
    wallLastFrameTimestamp = timestamp || Date.now();
    wallControlViewer.classList.add("has-frame");
  };
  img.src = `data:image/jpeg;base64,${msg.image}`;
}

function parseBinaryFrame(data) {
  const buffer = data instanceof ArrayBuffer ? data : data.buffer?.slice(data.byteOffset || 0, (data.byteOffset || 0) + data.byteLength);
  if (!buffer || buffer.byteLength < binaryFrameMagic.length + 4) return null;
  const prefix = textDecoder.decode(new Uint8Array(buffer, 0, binaryFrameMagic.length));
  if (prefix !== binaryFrameMagic) return null;
  const view = new DataView(buffer);
  const headerLength = view.getUint32(binaryFrameMagic.length, true);
  const headerStart = binaryFrameMagic.length + 4;
  const dataStart = headerStart + headerLength;
  if (headerLength < 2 || dataStart > buffer.byteLength) return null;
  const header = JSON.parse(textDecoder.decode(new Uint8Array(buffer, headerStart, headerLength)));
  return {
    header,
    image: new Uint8Array(buffer, dataStart)
  };
}

async function handleBinaryFrame(data, source = "relay") {
  let frame;
  try {
    frame = parseBinaryFrame(data);
  } catch {
    return;
  }
  if (!frame?.header) return;
  const header = frame.header;
  const relayRtcDevice = source === "relay" && supportsRtc(devices.find((item) => item.id === header.deviceId)) && rtcState?.deviceId === header.deviceId;
  const skipActiveRelay = relayRtcDevice && header.deviceId === activeDeviceId;
  const isActiveFrame = !skipActiveRelay && header.deviceId === activeDeviceId && screenOpen;
  const isMonitorFrame = !relayRtcDevice && activeStageTab === "wall" && shouldKeepMonitoring(header.deviceId);
  const isWallControlFrame = !relayRtcDevice && header.deviceId === wallActiveDeviceId && isWallControlOpen();
  if (!isActiveFrame && !isMonitorFrame && !isWallControlFrame) return;
  if (header.frameKind && header.frameKind !== "jpeg") return;
  const timestamp = Number(header.timestamp || 0);
  const activeDrawSerial = isActiveFrame ? ++frameDrawSerial : 0;
  const monitorSerial = isMonitorFrame ? (previewFrameSerials.get(header.deviceId) || 0) + 1 : 0;
  if (isMonitorFrame) previewFrameSerials.set(header.deviceId, monitorSerial);
  let bitmap;
  try {
    bitmap = await createImageBitmap(new Blob([frame.image], { type: "image/jpeg" }));
  } catch {
    return;
  }
  if (isActiveFrame) {
    if (timestamp && timestamp < lastFrameTimestamp) {
      bitmap.close();
      return;
    }
    if (activeDrawSerial === frameDrawSerial) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      frameSize = { width: bitmap.width, height: bitmap.height };
      viewer.style.aspectRatio = `${bitmap.width} / ${bitmap.height}`;
      ctx.drawImage(bitmap, 0, 0);
      lastFrameTimestamp = timestamp || Date.now();
      viewer.classList.add("has-frame");
    }
  }
  if (isMonitorFrame) {
    drawWallBitmapFrame(header, bitmap, monitorSerial);
  }
  if (isWallControlFrame) {
    drawWallControlBitmapFrame(header, bitmap);
  }
  bitmap.close();
  if (source === "relay" && ws && ws.readyState === WebSocket.OPEN) {
    const ackSessionId = header.deviceId === activeDeviceId && screenOpen ? sessionId : "";
    if (ackSessionId) {
      ws.send(JSON.stringify({ type: "frame-ack", sessionId: ackSessionId, deviceId: header.deviceId, frameId: header.frameId || timestamp }));
    }
  }
}

function drawWallBitmapFrame(header, bitmap, serial) {
  const deviceId = header.deviceId;
  if (serial && previewFrameSerials.get(deviceId) !== serial) return;
  const targetCanvas = screenWallGrid.querySelector(`canvas[data-wall-device="${cssEscape(deviceId)}"]`);
  if (!targetCanvas) return;
  const timestamp = Number(header.timestamp || 0);
  const lastTimestamp = Number(previewFrameTimestamps.get(deviceId) || 0);
  if (timestamp && timestamp < lastTimestamp) return;
  targetCanvas.width = bitmap.width;
  targetCanvas.height = bitmap.height;
  targetCanvas.getContext("2d").drawImage(bitmap, 0, 0);
  previewFrameTimestamps.set(deviceId, timestamp || Date.now());
  targetCanvas.closest(".wall-screen")?.classList.add("has-frame");
}

function drawWallControlBitmapFrame(header, bitmap) {
  const timestamp = Number(header.timestamp || 0);
  if (timestamp && timestamp < wallLastFrameTimestamp) return;
  wallControlCanvas.width = bitmap.width;
  wallControlCanvas.height = bitmap.height;
  wallFrameSize = { width: bitmap.width, height: bitmap.height };
  wallControlCtx.drawImage(bitmap, 0, 0);
  wallLastFrameTimestamp = timestamp || Date.now();
  wallControlViewer.classList.add("has-frame");
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  return {
    x: Math.round(x * frameSize.width),
    y: Math.round(y * frameSize.height)
  };
}

function wallCanvasPoint(event) {
  const rect = wallControlCanvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  return {
    x: Math.round(x * wallFrameSize.width),
    y: Math.round(y * wallFrameSize.height)
  };
}

function updateRemoteCursor(event) {
  const rect = canvas.getBoundingClientRect();
  const viewerRect = viewer.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left)) + rect.left - viewerRect.left;
  const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top)) + rect.top - viewerRect.top;
  remoteCursor.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  remoteCursor.classList.remove("hidden");
}

function hideRemoteCursor() {
  remoteCursor.classList.add("hidden");
}

function sendInput(payload) {
  if (!screenOpen || !sessionId || !inputReady || !ws || ws.readyState !== WebSocket.OPEN) {
    if (screenOpen && !sessionId) resumeActiveControl();
    if (sessionId && !inputReady) {
      const activeDevice = devices.find((item) => item.id === activeDeviceId);
      activeMeta.textContent = `当前仅可观看：${controlBlockReason(activeDevice) || "控制未就绪"}`;
    }
    return;
  }
  const message = { type: "input", sessionId, inputId: `h5-${Date.now()}-${++inputSerial}`, ...payload };
  if (rtcState?.sessionId === sessionId) {
    if (rtcState.controlChannel?.readyState === "open") {
      rtcState.controlChannel.send(JSON.stringify(message));
    } else {
      activeMeta.textContent = "直连控制通道连接中";
    }
    return;
  }
  ws.send(JSON.stringify(message));
}

function sendWallInput(payload) {
  const wallSessionId = activeWallSessionId();
  if (!isWallControlOpen() || !wallSessionId || !wallInputReady || !ws || ws.readyState !== WebSocket.OPEN) {
    updateWallControlState();
    return;
  }
  ws.send(JSON.stringify({ type: "input", sessionId: wallSessionId, inputId: `h5-${Date.now()}-${++inputSerial}`, ...payload }));
}

function isTypingTarget(target) {
  const tag = String(target?.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target?.isContentEditable;
}

function flushPendingText() {
  clearTimeout(pendingTextTimer);
  if (!pendingText) return;
  const text = pendingText;
  pendingText = "";
  sendInput({ action: "text", text });
}

function flushWallPendingText() {
  clearTimeout(wallPendingTextTimer);
  if (!wallPendingText) return;
  const text = wallPendingText;
  wallPendingText = "";
  sendWallInput({ action: "text", text });
}

function queueTextInput(text) {
  pendingText += text;
  clearTimeout(pendingTextTimer);
  pendingTextTimer = setTimeout(flushPendingText, 60);
}

function queueWallTextInput(text) {
  wallPendingText += text;
  clearTimeout(wallPendingTextTimer);
  wallPendingTextTimer = setTimeout(flushWallPendingText, 60);
}

function keyModifiers(event) {
  const modifiers = [];
  if (event.ctrlKey) modifiers.push("ctrl");
  if (event.altKey) modifiers.push("alt");
  if (event.shiftKey) modifiers.push("shift");
  if (event.metaKey) modifiers.push("meta");
  return modifiers;
}

function shouldForwardKeyboard(event) {
  if (!screenOpen || !sessionId || !inputReady) return false;
  if (isTypingTarget(event.target)) return false;
  return true;
}

function shouldForwardWallKeyboard(event) {
  if (!isWallControlOpen() || !activeWallSessionId() || !wallInputReady) return false;
  if (isTypingTarget(event.target)) return false;
  return event.target === wallControlCanvas || document.activeElement === wallControlCanvas;
}

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authSubmitBtn.disabled = true;
  try {
    const path = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
    const data = await api(path, {
      method: "POST",
      body: { username: usernameInput.value.trim(), password: passwordInput.value }
    });
    if (authMode === "register") {
      authHint.textContent = "注册成功，等待管理员审核后再登录使用。";
      setAuthMode("login");
      return;
    }
    sessionToken = data.token;
    localStorage.setItem("bhzn_session_token", sessionToken);
    setUser(data.user);
    if (data.user.status === "approved") {
      connect();
      const list = await api("/api/devices");
      devices = list.devices || [];
      renderDevices();
      syncMonitorSessions();
      renderScreenWall();
      await loadFileTransfers();
      if (requestedDeviceId) {
        setTimeout(() => startControl(requestedDeviceId), 300);
      }
    }
  } catch (error) {
    authHint.textContent = errorText(error.data || { error: error.message });
  } finally {
    authSubmitBtn.disabled = false;
  }
});

loginModeBtn.addEventListener("click", () => setAuthMode("login"));
registerModeBtn.addEventListener("click", () => setAuthMode("register"));
controlTabBtn.addEventListener("click", () => setStageTab("control"));
wallTabBtn.addEventListener("click", () => setStageTab("wall"));
filesTabBtn.addEventListener("click", () => setStageTab("files"));

accountMenuBtn.addEventListener("click", (event) => {
  event.stopPropagation();
  const nextOpen = accountDropdown.classList.contains("hidden");
  accountDropdown.classList.toggle("hidden", !nextOpen);
  accountMenuBtn.setAttribute("aria-expanded", nextOpen ? "true" : "false");
});

document.addEventListener("click", (event) => {
  if (accountDropdown.classList.contains("hidden")) return;
  if (accountDropdown.contains(event.target) || accountMenuBtn.contains(event.target)) return;
  accountDropdown.classList.add("hidden");
  accountMenuBtn.setAttribute("aria-expanded", "false");
});

logoutBtn.addEventListener("click", async () => {
  accountDropdown.classList.add("hidden");
  accountMenuBtn.setAttribute("aria-expanded", "false");
  try {
    if (sessionToken) await api("/api/auth/logout", { method: "POST" });
  } catch {
    // Local logout still wins.
  }
  sessionToken = "";
  currentUser = null;
  devices = [];
  fileTransfers = [];
  renderFileTransfers();
  localStorage.removeItem("bhzn_session_token");
  clearMonitorSessions(true, true);
  closeSocket();
  closeScreen(false);
  setAuthMode("login");
  setUser(null);
  showLogin("请先登录");
});

addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  bindHint.textContent = "正在匹配设备";
  try {
    const data = await api("/api/bindings", {
      method: "POST",
      body: {
        deviceId: deviceIdInput.value.trim().toUpperCase(),
        verificationCode: deviceCodeInput.value.trim()
      }
    });
    devices = data.devices || devices;
    bindHint.textContent = "设备已添加";
    renderDevices();
    startControl(data.device.id);
  } catch (error) {
    bindHint.textContent = errorText(error.data || { error: error.message });
  }
});

fileTransferForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const deviceId = String(fileDeviceSelect.value || "").trim();
  const file = fileInput.files && fileInput.files[0];
  if (!deviceId) {
    fileHint.textContent = "请选择在线设备";
    return;
  }
  if (!file) {
    fileHint.textContent = "请选择要下发的文件";
    return;
  }
  fileSubmitBtn.disabled = true;
  fileHint.textContent = `正在上传 ${file.name}`;
  try {
    const data = await apiRaw("/api/file-transfers", file, {
      headers: {
        "content-type": "application/octet-stream",
        "x-device-id": deviceId,
        "x-file-name": encodeURIComponent(file.name)
      }
    });
    upsertFileTransfer(data.transfer);
    fileInput.value = "";
  } catch (error) {
    fileHint.textContent = errorText(error.data || { error: error.message });
  } finally {
    renderFileTargets();
  }
});

canvas.addEventListener("pointerdown", (event) => {
  if (!sessionId || frameSize.width === 0) return;
  event.preventDefault();
  canvas.focus({ preventScroll: true });
  canvas.setPointerCapture(event.pointerId);
  updateRemoteCursor(event);
  const activeDevice = devices.find((item) => item.id === activeDeviceId);
  dragButton = event.button === 2 && isDesktopDevice(activeDevice) ? "right" : "left";
  dragStart = { ...canvasPoint(event), t: performance.now() };
  dragLast = dragStart;
  dragLastSentAt = dragStart.t;
  dragMoved = false;
  dragStartedRemote = false;
});

canvas.addEventListener("pointermove", (event) => {
  if (!dragStart || !dragLast) return;
  event.preventDefault();
  const now = performance.now();
  const activeDevice = devices.find((item) => item.id === activeDeviceId);
  const desktopDrag = isDesktopDevice(activeDevice);
  const minInterval = desktopDrag ? 10 : 55;
  if (now - dragLastSentAt < minInterval) return;
  const point = canvasPoint(event);
  updateRemoteCursor(event);
  const dxTotal = Math.abs(point.x - dragStart.x);
  const dyTotal = Math.abs(point.y - dragStart.y);
  const dx = Math.abs(point.x - dragLast.x);
  const dy = Math.abs(point.y - dragLast.y);
  const startThreshold = desktopDrag ? 1 : 10;
  const moveThreshold = desktopDrag ? 1 : 4;
  if (dxTotal < startThreshold && dyTotal < startThreshold) return;
  if (dx < moveThreshold && dy < moveThreshold) return;
  if (!dragStartedRemote) {
    sendInput({ action: "dragStart", x: dragStart.x, y: dragStart.y, duration: 16, button: dragButton });
    dragStartedRemote = true;
  }
  sendInput({
    action: "dragMove",
    x: point.x,
    y: point.y,
    duration: Math.max(16, Math.min(80, Math.round(now - dragLastSentAt))),
    button: dragButton
  });
  dragMoved = true;
  dragLast = point;
  dragLastSentAt = now;
});

window.addEventListener("beforeinput", (event) => {
  if (shouldForwardWallKeyboard(event)) {
    if (event.inputType !== "insertText" || !event.data) return;
    event.preventDefault();
    queueWallTextInput(event.data);
    return;
  }
  if (!shouldForwardKeyboard(event)) return;
  if (event.inputType !== "insertText" || !event.data) return;
  event.preventDefault();
  queueTextInput(event.data);
});

window.addEventListener("keydown", (event) => {
  if (shouldForwardWallKeyboard(event)) {
    const printable = event.key && event.key.length === 1;
    const hasCommandModifier = event.ctrlKey || event.altKey || event.metaKey;
    if (printable && !hasCommandModifier) {
      event.preventDefault();
      queueWallTextInput(event.key);
      return;
    }
    flushWallPendingText();
    event.preventDefault();
    sendWallInput({
      action: "key",
      key: event.key,
      code: event.code,
      modifiers: keyModifiers(event)
    });
    return;
  }
  if (!shouldForwardKeyboard(event)) return;
  const printable = event.key && event.key.length === 1;
  const hasCommandModifier = event.ctrlKey || event.altKey || event.metaKey;
  if (printable && !hasCommandModifier) {
    event.preventDefault();
    queueTextInput(event.key);
    return;
  }
  flushPendingText();
  event.preventDefault();
  sendInput({
    action: "key",
    key: event.key,
    code: event.code,
    modifiers: keyModifiers(event)
  });
});

canvas.addEventListener("pointerup", (event) => {
  if (!dragStart) return;
  event.preventDefault();
  const end = canvasPoint(event);
  updateRemoteCursor(event);
  const dx = Math.abs(end.x - dragStart.x);
  const dy = Math.abs(end.y - dragStart.y);
  const now = performance.now();
  const button = dragButton;
  if (!dragMoved && dx < 12 && dy < 12) {
    sendInput({ action: button === "right" ? "rightClick" : "tap", x: end.x, y: end.y, duration: 80, button });
  } else if (dragLast && dragStartedRemote) {
    sendInput({
      action: "dragEnd",
      x: end.x,
      y: end.y,
      duration: Math.max(16, Math.min(100, Math.round(now - dragLastSentAt))),
      button
    });
  }
  dragStart = null;
  dragLast = null;
  dragMoved = false;
  dragStartedRemote = false;
  dragButton = "left";
  setTimeout(hideRemoteCursor, 180);
});

canvas.addEventListener("pointercancel", () => {
  dragStart = null;
  dragLast = null;
  dragMoved = false;
  dragStartedRemote = false;
  dragButton = "left";
  hideRemoteCursor();
});

canvas.addEventListener("contextmenu", (event) => {
  event.preventDefault();
});

canvas.addEventListener("wheel", (event) => {
  const activeDevice = devices.find((item) => item.id === activeDeviceId);
  if (!isDesktopDevice(activeDevice)) return;
  event.preventDefault();
  const point = canvasPoint(event);
  sendInput({
    action: "scroll",
    x: point.x,
    y: point.y,
    deltaX: Math.round(event.deltaX),
    deltaY: Math.round(event.deltaY)
  });
}, { passive: false });

wallControlCanvas.addEventListener("pointerdown", (event) => {
  if (!activeWallSessionId() || wallFrameSize.width === 0) return;
  event.preventDefault();
  wallControlCanvas.focus({ preventScroll: true });
  wallControlCanvas.setPointerCapture(event.pointerId);
  const activeDevice = devices.find((item) => item.id === wallActiveDeviceId);
  wallDragButton = event.button === 2 && isDesktopDevice(activeDevice) ? "right" : "left";
  wallDragStart = { ...wallCanvasPoint(event), t: performance.now() };
  wallDragLast = wallDragStart;
  wallDragLastSentAt = wallDragStart.t;
  wallDragMoved = false;
  wallDragStartedRemote = false;
});

wallControlCanvas.addEventListener("pointermove", (event) => {
  if (!wallDragStart || !wallDragLast) return;
  event.preventDefault();
  const now = performance.now();
  const activeDevice = devices.find((item) => item.id === wallActiveDeviceId);
  const desktopDrag = isDesktopDevice(activeDevice);
  const minInterval = desktopDrag ? 10 : 55;
  if (now - wallDragLastSentAt < minInterval) return;
  const point = wallCanvasPoint(event);
  const dxTotal = Math.abs(point.x - wallDragStart.x);
  const dyTotal = Math.abs(point.y - wallDragStart.y);
  const dx = Math.abs(point.x - wallDragLast.x);
  const dy = Math.abs(point.y - wallDragLast.y);
  const startThreshold = desktopDrag ? 1 : 10;
  const moveThreshold = desktopDrag ? 1 : 4;
  if (dxTotal < startThreshold && dyTotal < startThreshold) return;
  if (dx < moveThreshold && dy < moveThreshold) return;
  if (!wallDragStartedRemote) {
    sendWallInput({ action: "dragStart", x: wallDragStart.x, y: wallDragStart.y, duration: 16, button: wallDragButton });
    wallDragStartedRemote = true;
  }
  sendWallInput({
    action: "dragMove",
    x: point.x,
    y: point.y,
    duration: Math.max(16, Math.min(80, Math.round(now - wallDragLastSentAt))),
    button: wallDragButton
  });
  wallDragMoved = true;
  wallDragLast = point;
  wallDragLastSentAt = now;
});

wallControlCanvas.addEventListener("pointerup", (event) => {
  if (!wallDragStart) return;
  event.preventDefault();
  const end = wallCanvasPoint(event);
  const dx = Math.abs(end.x - wallDragStart.x);
  const dy = Math.abs(end.y - wallDragStart.y);
  const now = performance.now();
  const button = wallDragButton;
  if (!wallDragMoved && dx < 12 && dy < 12) {
    sendWallInput({ action: button === "right" ? "rightClick" : "tap", x: end.x, y: end.y, duration: 80, button });
  } else if (wallDragLast && wallDragStartedRemote) {
    sendWallInput({
      action: "dragEnd",
      x: end.x,
      y: end.y,
      duration: Math.max(16, Math.min(100, Math.round(now - wallDragLastSentAt))),
      button
    });
  }
  wallDragStart = null;
  wallDragLast = null;
  wallDragMoved = false;
  wallDragStartedRemote = false;
  wallDragButton = "left";
});

wallControlCanvas.addEventListener("pointercancel", () => {
  wallDragStart = null;
  wallDragLast = null;
  wallDragMoved = false;
  wallDragStartedRemote = false;
  wallDragButton = "left";
});

wallControlCanvas.addEventListener("contextmenu", (event) => {
  event.preventDefault();
});

wallControlCanvas.addEventListener("wheel", (event) => {
  const activeDevice = devices.find((item) => item.id === wallActiveDeviceId);
  if (!isDesktopDevice(activeDevice)) return;
  event.preventDefault();
  const point = wallCanvasPoint(event);
  sendWallInput({
    action: "scroll",
    x: point.x,
    y: point.y,
    deltaX: Math.round(event.deltaX),
    deltaY: Math.round(event.deltaY)
  });
}, { passive: false });

for (const handle of wallResizeHandles) {
  handle.addEventListener("pointerdown", beginWallResize);
  handle.addEventListener("pointermove", moveWallResize);
  handle.addEventListener("pointerup", endWallResize);
  handle.addEventListener("pointercancel", endWallResize);
}

window.addEventListener("pointermove", moveWallResize);
window.addEventListener("pointerup", endWallResize);
window.addEventListener("pointercancel", endWallResize);
window.addEventListener("pointermove", moveWallTileResize);
window.addEventListener("pointerup", endWallTileResize);
window.addEventListener("pointercancel", endWallTileResize);

window.addEventListener("resize", () => {
  if (isWallControlOpen()) normalizeWallControlBounds();
});

closeScreenBtn.addEventListener("click", () => closeScreen(true));
if (qualitySelect) {
  qualitySelect.value = qualityProfiles[selectedQualityProfile] ? selectedQualityProfile : "balanced";
  qualitySelect.addEventListener("change", () => setQualityProfile(qualitySelect.value, true));
}
fullscreenBtn.addEventListener("click", toggleViewerFullscreen);
document.addEventListener("fullscreenchange", () => {
  fullscreenBtn.textContent = isViewerFullscreen() ? "恢复" : "全屏";
  wallControlFullscreenBtn.textContent = document.fullscreenElement === wallControlWindow ? "恢复" : "全屏";
});
backBtn.addEventListener("click", () => sendInput({ action: "back" }));
homeBtn.addEventListener("click", () => sendInput({ action: "home" }));
homeSwipeBtn.addEventListener("click", () => {
  if (!frameSize.width || !frameSize.height) return;
  sendInput({
    action: "homeSwipe",
    x: Math.round(frameSize.width / 2),
    y: Math.max(1, frameSize.height - 8),
    x2: Math.round(frameSize.width / 2),
    y2: Math.round(frameSize.height * 0.55),
    duration: 320
  });
});
wallControlCloseBtn.addEventListener("click", () => closeWallControl());
wallControlFullscreenBtn.addEventListener("click", toggleWallControlFullscreen);
wallControlBackBtn.addEventListener("click", () => sendWallInput({ action: "back" }));
wallControlHomeBtn.addEventListener("click", () => sendWallInput({ action: "home" }));
wallControlHomeSwipeBtn.addEventListener("click", () => {
  if (!wallFrameSize.width || !wallFrameSize.height) return;
  sendWallInput({
    action: "homeSwipe",
    x: Math.round(wallFrameSize.width / 2),
    y: Math.max(1, wallFrameSize.height - 8),
    x2: Math.round(wallFrameSize.width / 2),
    y2: Math.round(wallFrameSize.height * 0.55),
    duration: 320
  });
});
stopBtn.addEventListener("click", () => {
  if (!sessionId) return;
  if (rtcState?.sessionId === sessionId) {
    closeRtc("controller_stopped", true);
  } else {
    ws.send(JSON.stringify({ type: "stop-control", sessionId }));
  }
  sessionId = "";
  setControls(false);
});

async function boot() {
  setAuthMode("login");
  showLogin();
  closeScreen(false);
  if (!sessionToken) {
    setUser(null);
    return;
  }
  try {
    const data = await api("/api/me");
    setUser(data.user);
    if (data.user.status === "approved") {
      connect();
      const list = await api("/api/devices");
      devices = list.devices || [];
      renderDevices();
      syncMonitorSessions();
      renderScreenWall();
      await loadFileTransfers();
    }
  } catch {
    sessionToken = "";
    localStorage.removeItem("bhzn_session_token");
    setUser(null);
  }
}

boot();
