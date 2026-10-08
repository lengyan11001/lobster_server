const cmsState = document.getElementById("cmsState");
const adminForm = document.getElementById("adminForm");
const adminUsernameInput = document.getElementById("adminUsernameInput");
const adminPasswordInput = document.getElementById("adminPasswordInput");
const adminSubmitBtn = document.getElementById("adminSubmitBtn");
const adminLogoutBtn = document.getElementById("adminLogoutBtn");
const cmsContent = document.getElementById("cmsContent");
const userTable = document.getElementById("userTable");
const bindingTable = document.getElementById("bindingTable");
const accountConnectionTable = document.getElementById("accountConnectionTable");
const linkTable = document.getElementById("linkTable");
const remoteDeviceTable = document.getElementById("remoteDeviceTable");

let adminToken = localStorage.getItem("bhzn_admin_session") || "";
let cmsRefreshTimer = 0;

function statusText(status) {
  if (status === "approved") return "已通过";
  if (status === "rejected") return "已拒绝";
  return "待审核";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function adminApi(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(adminToken ? { authorization: `Bearer ${adminToken}` } : {}),
      ...(options.headers || {})
    },
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

function setLoggedIn(admin) {
  cmsContent.classList.remove("hidden");
  adminUsernameInput.classList.add("hidden");
  adminPasswordInput.classList.add("hidden");
  adminSubmitBtn.classList.add("hidden");
  adminLogoutBtn.classList.remove("hidden");
  cmsState.textContent = `${admin.username} 已登录`;
}

function setLoggedOut(message = "请登录管理员账号") {
  adminToken = "";
  clearTimeout(cmsRefreshTimer);
  localStorage.removeItem("bhzn_admin_session");
  cmsContent.classList.add("hidden");
  adminUsernameInput.classList.remove("hidden");
  adminPasswordInput.classList.remove("hidden");
  adminSubmitBtn.classList.remove("hidden");
  adminLogoutBtn.classList.add("hidden");
  userTable.innerHTML = "";
  bindingTable.innerHTML = "";
  if (accountConnectionTable) accountConnectionTable.innerHTML = "";
  if (linkTable) linkTable.innerHTML = "";
  if (remoteDeviceTable) remoteDeviceTable.innerHTML = "";
  cmsState.textContent = message;
}

function renderRemoteDevices(devices) {
  if (!remoteDeviceTable) return;
  if (!devices.length) {
    remoteDeviceTable.innerHTML = `<div class="placeholder-card">暂无已打开远程支持的在线设备</div>`;
    return;
  }
  remoteDeviceTable.innerHTML = "";
  for (const device of devices) {
    const row = document.createElement("div");
    row.className = "table-row";
    const owners = (device.owners || []).map((owner) => escapeHtml(owner.username)).join(", ") || "未绑定账号";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(device.name || device.id)}</strong>
        <span>设备编号：${escapeHtml(device.id)} · ${escapeHtml(device.platform || "-")} · ${escapeHtml(device.agentVersion || "Agent")}</span>
        <span>绑定账号：${owners}</span>
      </div>
      <div class="mini-badges"><i class="badge good">已打开</i><i class="badge">${device.controlEnabled ? "可控制" : "仅查看"}</i></div>
    `;
    remoteDeviceTable.appendChild(row);
  }
}

function renderUsers(users) {
  if (!users.length) {
    userTable.innerHTML = `<div class="placeholder-card">暂无注册用户</div>`;
    return;
  }
  userTable.innerHTML = "";
  for (const user of users) {
    const row = document.createElement("div");
    row.className = "table-row";
    row.innerHTML = `
      <div>
        <strong>${user.username}</strong>
        <span>${statusText(user.status)} · ${new Date(user.createdAt).toLocaleString()}</span>
      </div>
      <div class="row-actions">
        <button data-action="approved" ${user.status === "approved" ? "disabled" : ""}>通过</button>
        <button data-action="rejected" class="danger-small" ${user.status === "rejected" ? "disabled" : ""}>拒绝</button>
      </div>
    `;
    row.querySelectorAll("button[data-action]").forEach((button) => {
      button.addEventListener("click", () => updateUserStatus(user.id, button.dataset.action));
    });
    userTable.appendChild(row);
  }
}

function renderBindings(bindings) {
  if (!bindings.length) {
    bindingTable.innerHTML = `<div class="placeholder-card">暂无设备绑定记录</div>`;
    return;
  }
  bindingTable.innerHTML = "";
  for (const binding of bindings) {
    const row = document.createElement("div");
    row.className = "table-row";
    row.innerHTML = `
      <div>
        <strong>${binding.deviceId}</strong>
        <span>${binding.username} · ${binding.online ? "在线" : "离线"} · ${binding.label || "Android Device"}</span>
      </div>
      <div class="mini-badges">
        <i class="badge ${binding.permissions?.mediaProjection ? "" : "warn"}">录屏${binding.permissions?.mediaProjection ? "已开" : "未开"}</i>
        <i class="badge ${binding.permissions?.accessibility ? "" : "warn"}">输入${binding.permissions?.accessibility ? "已开" : "未开"}</i>
      </div>
    `;
    bindingTable.appendChild(row);
  }
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function pathText(path) {
  if (path === "rtc-lan") return "局域网直连";
  if (path === "rtc-p2p") return "公网 P2P";
  if (path === "rtc-turn") return "TURN UDP";
  if (path === "ws-relay") return "WebSocket 中转";
  if (path === "rtc") return "WebRTC 连接中";
  if (path === "online") return "在线待命";
  return "离线";
}

function pathClass(path) {
  if (path === "rtc-lan" || path === "rtc-p2p") return "good";
  if (path === "rtc-turn") return "warn";
  if (path === "ws-relay") return "danger";
  return "";
}

function modeText(mode) {
  if (mode === "monitor") return "屏幕墙";
  if (mode === "view") return "观看";
  if (mode === "file") return "文件";
  return "远程控制";
}

function sessionModeSummary(item) {
  const modes = item.modes || [];
  if (!modes.length) return "无活动会话";
  return modes.map(modeText).join(" / ");
}

function renderAccountConnections(accounts) {
  if (!accountConnectionTable) return;
  if (!accounts.length) {
    accountConnectionTable.innerHTML = `<div class="placeholder-card">暂无账号连接数据</div>`;
    return;
  }
  accountConnectionTable.innerHTML = "";
  for (const account of accounts) {
    const row = document.createElement("div");
    row.className = "table-row account-connection-row";
    const devices = account.devices || [];
    const deviceLines = devices.length ? devices.map((device) => {
      const sessionText = device.activeSessionCount
        ? `${device.activeSessionCount} 个会话 · ${sessionModeSummary(device)} · RTC ${device.rtcSessionCount || 0} · 中转 ${device.relaySessionCount || 0}`
        : "未连接";
      return `
        <div class="account-device-line">
          <div>
            <strong>${escapeHtml(device.deviceId || device.id)}</strong>
            <span>${escapeHtml(device.label || device.name || device.platform || "-")} · ${device.online ? "在线" : "离线"} · ${escapeHtml(device.agentVersion || "Agent -")}</span>
          </div>
          <div class="account-device-meta">
            <i class="badge ${pathClass(device.activePath)}">${pathText(device.activePath)}</i>
            <span>${escapeHtml(sessionText)}</span>
            <span>录屏${device.permissions?.mediaProjection ? "已开" : "未开"} · 输入${device.permissions?.accessibility ? "已开" : "未开"}</span>
          </div>
        </div>
      `;
    }).join("") : `<div class="placeholder-card compact">暂无绑定设备</div>`;
    row.innerHTML = `
      <div class="account-summary">
        <div>
          <strong>${escapeHtml(account.username)}</strong>
          <span>${statusText(account.status)} · 绑定 ${account.boundDeviceCount || 0} · 在线 ${account.onlineDeviceCount || 0}</span>
        </div>
        <div class="mini-badges">
          <i class="badge ${account.activeSessionCount ? "good" : ""}">活跃 ${account.activeSessionCount || 0}</i>
          <i class="badge">控制 ${account.controlSessionCount || 0}</i>
          <i class="badge">监控 ${account.monitorSessionCount || 0}</i>
          <i class="badge ${account.rtcSessionCount ? "good" : ""}">RTC ${account.rtcSessionCount || 0}</i>
          <i class="badge ${account.relaySessionCount ? "danger" : ""}">中转 ${account.relaySessionCount || 0}</i>
        </div>
      </div>
      <div class="account-devices">${deviceLines}</div>
    `;
    accountConnectionTable.appendChild(row);
  }
}

function renderLinks(links) {
  if (!linkTable) return;
  if (!links.length) {
    linkTable.innerHTML = `<div class="placeholder-card">暂无设备链路数据</div>`;
    return;
  }
  linkTable.innerHTML = "";
  for (const item of links) {
    const link = item.link || {};
    const rtc = (link.activeRtcSessions || [])[0];
    const relay = (link.activeRelaySessions || [])[0];
    const quality = rtc?.quality || relay?.quality || {};
    const row = document.createElement("div");
    row.className = "table-row link-row";
    row.innerHTML = `
      <div>
        <strong>${item.deviceId}</strong>
        <span>${item.online ? "在线" : "离线"} · ${item.label || item.name || item.platform || "-"} · ${item.agentVersion || "Agent -"}</span>
        <span>owner: ${(item.owners || []).map((owner) => owner.username).join(", ") || "-"}</span>
      </div>
      <div class="link-fields">
        <i class="badge ${pathClass(link.activePath)}">${pathText(link.activePath)}</i>
        <b>${quality.profile || "-"} · ${quality.maxSide || "-"}px · ${quality.fps || "-"}fps</b>
        <span>relay: ${formatBytes(link.bytesFromDevice)} / ${formatBytes(link.bytesToControllers)}</span>
        <span>rtc: ${formatBytes(link.rtcBytesSent)} / ${formatBytes(link.rtcBytesReceived)} · ${Math.round(link.rtcBitrateKbps || 0)} kbps · ${Math.round(link.rtcRttMs || 0)} ms</span>
        <span>会话: RTC ${(link.activeRtcSessions || []).length} · 中转 ${(link.activeRelaySessions || []).length}${relay?.ttlSeconds ? ` · 中转剩余 ${Math.ceil(relay.ttlSeconds / 60)} 分钟` : ""}</span>
      </div>
    `;
    linkTable.appendChild(row);
  }
}

async function loadCms() {
  if (!adminToken) {
    setLoggedOut();
    return;
  }
  cmsState.textContent = "正在加载";
  try {
    const [me, users, bindings, accountConnections, links, remoteDevices] = await Promise.all([
      adminApi("/api/admin/me"),
      adminApi("/api/admin/users"),
      adminApi("/api/admin/bindings"),
      adminApi("/api/admin/account-connections"),
      adminApi("/api/admin/device-links"),
      adminApi("/api/admin/remote-devices")
    ]);
    setLoggedIn(me.admin);
    renderUsers(users.users || []);
    renderBindings(bindings.bindings || []);
    renderAccountConnections(accountConnections.accounts || []);
    renderLinks(links.links || []);
    renderRemoteDevices(remoteDevices.devices || []);
    clearTimeout(cmsRefreshTimer);
    cmsRefreshTimer = setTimeout(loadCms, 5000);
  } catch {
    setLoggedOut("管理员登录已失效，请重新登录");
  }
}

async function updateUserStatus(id, status) {
  cmsState.textContent = "正在保存";
  try {
    await adminApi(`/api/admin/users/${id}/status`, {
      method: "POST",
      body: { status }
    });
    await loadCms();
  } catch {
    cmsState.textContent = "保存失败";
  }
}

adminForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  cmsState.textContent = "正在登录";
  try {
    const data = await adminApi("/api/admin/login", {
      method: "POST",
      body: {
        username: adminUsernameInput.value.trim(),
        password: adminPasswordInput.value
      }
    });
    adminToken = data.token;
    localStorage.setItem("bhzn_admin_session", adminToken);
    adminPasswordInput.value = "";
    await loadCms();
  } catch {
    setLoggedOut("管理员账号或密码错误");
  }
});

adminLogoutBtn.addEventListener("click", async () => {
  try {
    if (adminToken) await adminApi("/api/admin/logout", { method: "POST" });
  } catch {
    // Local logout still wins.
  }
  setLoggedOut();
});

loadCms();
