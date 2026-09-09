const crypto = require("crypto");
const express = require("express");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { WebSocket, WebSocketServer } = require("ws");

// Remote support owns a dedicated listener.  These ports belong to the main
// Lobster services and must never be reused by the relay, even if an old
// systemd override or environment variable is present.
// LOBSTER_REMOTE_PORT intentionally wins over a shared .env PORT=8000 from
// the main API. This prevents an old environment value from ever stealing a
// main-service listener.
const PORT = Number(process.env.LOBSTER_REMOTE_PORT || process.env.PORT || 38080);
const RESERVED_MAIN_PORTS = new Set([8000, 8001, 8010, 4111]);
if (RESERVED_MAIN_PORTS.has(PORT)) {
  console.error(`Refusing to start remote support on reserved main-service port ${PORT}; use 38080 or another dedicated port.`);
  process.exit(78);
}
const HOST = process.env.HOST || "127.0.0.1";
const PUBLIC_DIR = path.join(__dirname, "..", "public");
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, "..", "data");
const DATA_FILE = path.join(DATA_DIR, "state.json");
const DEVICE_TTL_MS = 120_000;
const WS_PING_INTERVAL_MS = 30_000;
const WS_MAX_MISSED_PONGS = 3;
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const FRAME_BUFFER_LIMIT = Number(process.env.FRAME_BUFFER_LIMIT || 0);
const BINARY_FRAME_MAGIC = Buffer.from("BHZF1");
const BINARY_FRAME_HEADER_LIMIT = 64 * 1024;
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";
const DEFAULT_ADMIN_USERNAME = process.env.DEFAULT_ADMIN_USERNAME || "admin";
const DEFAULT_ADMIN_PASSWORD = process.env.DEFAULT_ADMIN_PASSWORD || "todesk2026";
const WINDOWS_AGENT_VERSION = process.env.WINDOWS_AGENT_VERSION || "0.2.8-rs";
const WINDOWS_AGENT_FILE = process.env.WINDOWS_AGENT_FILE || "BHZN-ToDesk-Agent.exe";
const ANDROID_APK_VERSION = process.env.ANDROID_APK_VERSION || "0.1.18";
const ANDROID_APK_VERSION_CODE = Number(process.env.ANDROID_APK_VERSION_CODE || 19);
const ANDROID_APK_FILE = process.env.ANDROID_APK_FILE || "bhzn-todesk-debug.apk";
const MAC_AGENT_VERSION = process.env.MAC_AGENT_VERSION || "0.1.21";
const MAC_AGENT_ARM64_VERSION = process.env.MAC_AGENT_ARM64_VERSION || MAC_AGENT_VERSION;
const MAC_AGENT_X86_64_VERSION = process.env.MAC_AGENT_X86_64_VERSION || "0.1.19";
const MAC_AGENT_ARM64_FILE = process.env.MAC_AGENT_ARM64_FILE || "BHZN-ToDesk-Agent-mac-arm64.zip";
const MAC_AGENT_X86_64_FILE = process.env.MAC_AGENT_X86_64_FILE || "BHZN-ToDesk-Agent-mac-x86_64.zip";
const FILE_TRANSFER_MAX_BYTES = Number(process.env.FILE_TRANSFER_MAX_BYTES || 100 * 1024 * 1024);
const FILE_TRANSFER_TTL_MS = Number(process.env.FILE_TRANSFER_TTL_MS || 24 * 60 * 60 * 1000);
const FILE_TRANSFER_DIR = path.join(DATA_DIR, "file-transfers");
const RTC_SESSION_TTL_MS = Number(process.env.RTC_SESSION_TTL_MS || 60 * 60 * 1000);
const RELAY_SESSION_TTL_MS = Math.max(60_000, Number(process.env.RELAY_SESSION_TTL_MS || 10 * 60 * 1000));
const RTC_STUN_URLS = splitEnvList(process.env.RTC_STUN_URLS || "stun:stun.cloudflare.com:3478");
const RTC_TURN_URLS = splitEnvList(process.env.RTC_TURN_URLS || "");
const RTC_TURN_SECRET = process.env.RTC_TURN_SECRET || "";
const RTC_TURN_USERNAME = process.env.RTC_TURN_USERNAME || "";
const RTC_TURN_CREDENTIAL = process.env.RTC_TURN_CREDENTIAL || "";
const RTC_TURN_TTL_SECONDS = Math.max(60, Number(process.env.RTC_TURN_TTL_SECONDS || 3600));
// This relay is deployed beside the main Lobster server.  The main admin
// token is verified by FastAPI before it can create a controller session or
// change the explicitly authorised device list.
const MAIN_API_BASE = String(process.env.LOBSTER_REMOTE_MAIN_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
const REMOTE_SERVICE_KEY = String(process.env.LOBSTER_REMOTE_SERVICE_KEY || process.env.REMOTE_SUPPORT_SERVICE_KEY || "");
const PUBLIC_BASE_PATH = String(process.env.LOBSTER_REMOTE_PUBLIC_BASE_PATH || "/remote").replace(/\/$/, "");
const ALLOW_SELF_SERVICE = String(process.env.LOBSTER_REMOTE_ALLOW_SELF_SERVICE || "false").toLowerCase() === "true";
const MAIN_ADMIN_USER_ID = "lobster-main-admin";
const RELAY_QUALITY_PROFILE = Object.freeze({
  profile: "relay",
  maxSide: 1280,
  fps: 10,
  jpegQuality: 45,
  bitrateKbps: 1200
});

process.on("uncaughtException", (error) => {
  const code = error?.code || "";
  console.error("uncaught exception", {
    code,
    message: error?.message || String(error),
    stack: error?.stack || ""
  });
  if (code === "WS_ERR_UNSUPPORTED_MESSAGE_LENGTH") return;
  process.exit(1);
});

process.on("unhandledRejection", (error) => {
  console.error("unhandled rejection", {
    message: error?.message || String(error),
    stack: error?.stack || ""
  });
});

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "1mb" }));
app.use("/downloads", express.static(path.join(PUBLIC_DIR, "downloads"), {
  etag: false,
  lastModified: false,
  setHeaders(res) {
    res.setHeader("Cache-Control", "no-store, max-age=0");
  }
}));
app.use(express.static(PUBLIC_DIR, {
  extensions: ["html"],
  // The controller is opened from the admin console and must pick up the
  // current direct-control/fallback logic immediately after a deployment.
  // Caching an old app.js here can leave users on the pre-relay interaction.
  maxAge: 0,
  setHeaders(res, filePath) {
    if (/\.(?:html?|js|css)$/i.test(filePath)) {
      res.setHeader("Cache-Control", "no-store, max-age=0, must-revalidate");
      res.setHeader("Pragma", "no-cache");
    }
  }
}));

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: "/ws", maxPayload: 25 * 1024 * 1024 });

const devices = new Map();
const clients = new Set();
const controllers = new Map();
const rtcSessions = new Map();
const fileTransfers = new Map();
const trafficStats = new Map();
let state = loadState();

function now() {
  return Date.now();
}

function splitEnvList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function iso(time = now()) {
  return new Date(time).toISOString();
}

function safeJson(value) {
  return JSON.stringify(value);
}

function send(ws, payload) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(safeJson(payload));
  }
}

function sendRealtimeFrame(ws, payload) {
  if (ws.readyState !== WebSocket.OPEN) return false;
  if (ws.bufferedAmount > FRAME_BUFFER_LIMIT) return false;
  ws.send(safeJson(payload));
  return true;
}

function sendRealtimeBinaryFrame(ws, payload) {
  if (ws.readyState !== WebSocket.OPEN) return false;
  if (ws.bufferedAmount > FRAME_BUFFER_LIMIT) return false;
  ws.send(payload, { binary: true });
  return true;
}

function relayLog(event, details = {}) {
  console.log(JSON.stringify({ time: iso(), event, ...details }));
}

function trafficStatsFor(deviceId) {
  const id = normalizeDeviceId(deviceId);
  let stats = trafficStats.get(id);
  if (!stats) {
    stats = {
      deviceId: id,
      createdAt: iso(),
      updatedAt: iso(),
      framesFromDevice: 0,
      framesToControllers: 0,
      bytesFromDevice: 0,
      bytesToControllers: 0,
      lastFrameBytesFromDevice: 0,
      lastFrameBytesToControllers: 0,
      lastControllerCount: 0,
      rtcBytesSent: 0,
      rtcBytesReceived: 0,
      rtcBitrateKbps: 0,
      rtcRttMs: 0,
      rtcPacketsLost: 0,
      lastRtcCandidateType: "",
      lastRtcState: "",
      lastRtcUpdatedAt: ""
    };
    trafficStats.set(id, stats);
  }
  return stats;
}

function addFrameTraffic(deviceId, fromDeviceBytes, toControllersBytes, controllerCount) {
  const stats = trafficStatsFor(deviceId);
  stats.framesFromDevice += 1;
  stats.framesToControllers += controllerCount;
  stats.bytesFromDevice += fromDeviceBytes;
  stats.bytesToControllers += toControllersBytes;
  stats.lastFrameBytesFromDevice = fromDeviceBytes;
  stats.lastFrameBytesToControllers = toControllersBytes;
  stats.lastControllerCount = controllerCount;
  stats.updatedAt = iso();
}

function resetTrafficStats(deviceId) {
  const id = normalizeDeviceId(deviceId);
  const stats = {
    deviceId: id,
    createdAt: iso(),
    updatedAt: iso(),
    framesFromDevice: 0,
    framesToControllers: 0,
    bytesFromDevice: 0,
    bytesToControllers: 0,
    lastFrameBytesFromDevice: 0,
    lastFrameBytesToControllers: 0,
    lastControllerCount: 0,
    rtcBytesSent: 0,
    rtcBytesReceived: 0,
    rtcBitrateKbps: 0,
    rtcRttMs: 0,
    rtcPacketsLost: 0,
    lastRtcCandidateType: "",
    lastRtcState: "",
    lastRtcUpdatedAt: ""
  };
  trafficStats.set(id, stats);
  return stats;
}

function trafficStatsView(stats) {
  const device = devices.get(stats.deviceId);
  const activeRtc = activeRtcSessionsForDevice(stats.deviceId);
  const relaySessions = device ? Array.from(device.controllers).map((sessionId) => controllers.get(sessionId)).filter(Boolean) : [];
  return {
    ...stats,
    online: Boolean(device),
    controllerCount: device ? device.controllers.size : 0,
    activePath: activePathForDevice(device, stats, activeRtc, relaySessions),
    activeRtcSessions: activeRtc.map(adminRtcSessionView),
    activeRelaySessions: relaySessions.map(adminRelaySessionView)
  };
}

function qualityProfileFromValue(value, fallback = "balanced") {
  if (typeof value === "object" && value) value = value.profile || value.name || "";
  const profile = String(value || fallback).toLowerCase();
  if (profile === "lan") {
    return { profile: "lan", maxSide: 3840, fps: 30, jpegQuality: 74, bitrateKbps: 12000 };
  }
  if (profile === "hd") {
    return { profile: "hd", maxSide: 2560, fps: 24, jpegQuality: 66, bitrateKbps: 6000 };
  }
  if (profile === "data") {
    return { profile: "data", maxSide: 1280, fps: 10, jpegQuality: 42, bitrateKbps: 1200 };
  }
  return { profile: "balanced", maxSide: 1920, fps: 18, jpegQuality: 58, bitrateKbps: 3000 };
}

function sanitizeSessionMode(value) {
  const mode = String(value || "control").toLowerCase();
  if (["control", "monitor", "view", "file"].includes(mode)) return mode;
  return "control";
}

function activeRtcSessionsForDevice(deviceId) {
  return Array.from(rtcSessions.values()).filter((session) => session.deviceId === deviceId);
}

function activePathForDevice(device, stats, activeRtc, relaySessions) {
  if (activeRtc.length) {
    const selected = activeRtc.find((session) => session.selectedCandidateType && session.selectedCandidateType !== "unknown") || activeRtc[0];
    const type = selected.selectedCandidateType || stats.lastRtcCandidateType || "unknown";
    if (type === "host") return "rtc-lan";
    if (type === "srflx" || type === "prflx") return "rtc-p2p";
    if (type === "relay") return "rtc-turn";
    return "rtc";
  }
  if (relaySessions.length) return "ws-relay";
  if (device) return "online";
  return "offline";
}

function adminRtcSessionView(session) {
  return {
    sessionId: session.sessionId,
    userId: session.userId,
    deviceId: session.deviceId,
    mode: sanitizeSessionMode(session.mode),
    createdAt: iso(session.createdAt),
    expiresAt: iso(session.expiresAt),
    ttlSeconds: Math.max(0, Math.floor((session.expiresAt - now()) / 1000)),
    quality: session.quality,
    state: session.state || "",
    selectedCandidateType: session.selectedCandidateType || "unknown",
    rttMs: Number(session.rttMs || 0),
    bitrateKbps: Number(session.bitrateKbps || 0),
    bytesSent: Number(session.bytesSent || 0),
    bytesReceived: Number(session.bytesReceived || 0),
    packetsLost: Number(session.packetsLost || 0),
    updatedAt: session.updatedAt ? iso(session.updatedAt) : ""
  };
}

function adminRelaySessionView(session) {
  return {
    sessionId: session.sessionId,
    userId: session.userId,
    deviceId: session.deviceId,
    mode: sanitizeSessionMode(session.mode),
    reason: session.reason || "",
    createdAt: iso(session.createdAt),
    expiresAt: iso(session.expiresAt),
    ttlSeconds: Math.max(0, Math.floor((session.expiresAt - now()) / 1000)),
    quality: session.quality || RELAY_QUALITY_PROFILE
  };
}

function accountConnectionView(user) {
  const bindings = state.bindings.filter((binding) => binding.userId === user.id);
  const rtcForUser = Array.from(rtcSessions.values()).filter((session) => session.userId === user.id);
  const relayForUser = Array.from(controllers.values()).filter((session) => session.userId === user.id);
  const activeDeviceIds = new Set([
    ...rtcForUser.map((session) => session.deviceId),
    ...relayForUser.map((session) => session.deviceId)
  ]);
  const devicesForUser = bindings
    .slice()
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    .map((binding) => {
      const live = devices.get(binding.deviceId);
      const rtcSessionsForDevice = rtcForUser.filter((session) => session.deviceId === binding.deviceId);
      const relaySessionsForDevice = relayForUser.filter((session) => session.deviceId === binding.deviceId);
      const stats = trafficStatsFor(binding.deviceId);
      const sessionModes = Array.from(new Set([
        ...rtcSessionsForDevice.map((session) => sanitizeSessionMode(session.mode)),
        ...relaySessionsForDevice.map((session) => sanitizeSessionMode(session.mode))
      ]));
      return {
        ...bindingView(binding),
        activePath: activePathForDevice(live, stats, rtcSessionsForDevice, relaySessionsForDevice),
        activeSessionCount: rtcSessionsForDevice.length + relaySessionsForDevice.length,
        rtcSessionCount: rtcSessionsForDevice.length,
        relaySessionCount: relaySessionsForDevice.length,
        monitorSessionCount: sessionModes.includes("monitor")
          ? rtcSessionsForDevice.filter((session) => sanitizeSessionMode(session.mode) === "monitor").length
            + relaySessionsForDevice.filter((session) => sanitizeSessionMode(session.mode) === "monitor").length
          : 0,
        controlSessionCount: rtcSessionsForDevice.filter((session) => sanitizeSessionMode(session.mode) === "control").length
          + relaySessionsForDevice.filter((session) => sanitizeSessionMode(session.mode) === "control").length,
        modes: sessionModes,
        activeRtcSessions: rtcSessionsForDevice.map(adminRtcSessionView),
        activeRelaySessions: relaySessionsForDevice.map(adminRelaySessionView)
      };
    });
  return {
    userId: user.id,
    username: user.username,
    status: user.status,
    boundDeviceCount: bindings.length,
    onlineDeviceCount: devicesForUser.filter((item) => item.online).length,
    activeDeviceCount: activeDeviceIds.size,
    activeSessionCount: rtcForUser.length + relayForUser.length,
    rtcSessionCount: rtcForUser.length,
    relaySessionCount: relayForUser.length,
    monitorSessionCount: rtcForUser.filter((session) => sanitizeSessionMode(session.mode) === "monitor").length
      + relayForUser.filter((session) => sanitizeSessionMode(session.mode) === "monitor").length,
    controlSessionCount: rtcForUser.filter((session) => sanitizeSessionMode(session.mode) === "control").length
      + relayForUser.filter((session) => sanitizeSessionMode(session.mode) === "control").length,
    devices: devicesForUser
  };
}

function rtcIceServers(userId, deviceId, sessionId) {
  const iceServers = [];
  for (const url of RTC_STUN_URLS) {
    iceServers.push({ urls: [url] });
  }
  if (RTC_TURN_URLS.length) {
    if (RTC_TURN_SECRET) {
      const expireAt = Math.floor(now() / 1000) + RTC_TURN_TTL_SECONDS;
      const username = `${expireAt}:${userId}:${deviceId}:${sessionId}`;
      const credential = crypto.createHmac("sha1", RTC_TURN_SECRET).update(username).digest("base64");
      iceServers.push({ urls: RTC_TURN_URLS, username, credential });
    } else if (RTC_TURN_USERNAME && RTC_TURN_CREDENTIAL) {
      iceServers.push({ urls: RTC_TURN_URLS, username: RTC_TURN_USERNAME, credential: RTC_TURN_CREDENTIAL });
    }
  }
  return iceServers;
}

function publicRtcCapabilities(device) {
  const capabilities = device?.rtcCapabilities || {};
  return {
    webrtc: Boolean(capabilities.webrtc),
    video: Boolean(capabilities.video),
    dataChannel: Boolean(capabilities.dataChannel),
    frameChannel: Boolean(capabilities.frameChannel),
    localNetwork: capabilities.localNetwork !== false,
    codecs: Array.isArray(capabilities.codecs) ? capabilities.codecs.slice(0, 8).map(String) : [],
    maxFps: Number.isFinite(Number(capabilities.maxFps)) ? Math.max(0, Math.min(120, Number(capabilities.maxFps))) : 0,
    version: String(capabilities.version || "")
  };
}

function supportsRtcRelayBypass(device) {
  const capabilities = publicRtcCapabilities(device);
  return Boolean(capabilities.webrtc && capabilities.video);
}

function hasActiveRtcSessionForDevice(deviceId) {
  for (const session of rtcSessions.values()) {
    if (session.deviceId === deviceId) return true;
  }
  return false;
}

function sanitizeRtcCandidate(value) {
  if (!value || typeof value !== "object") return null;
  const candidate = String(value.candidate || "");
  if (!candidate || candidate.length > 4096) return null;
  return {
    candidate,
    sdpMid: value.sdpMid == null ? null : String(value.sdpMid).slice(0, 64),
    sdpMLineIndex: Number.isFinite(Number(value.sdpMLineIndex)) ? Number(value.sdpMLineIndex) : null,
    usernameFragment: value.usernameFragment == null ? undefined : String(value.usernameFragment).slice(0, 128)
  };
}

function sanitizeRtcSdp(value) {
  const sdp = String(value || "");
  if (!sdp || sdp.length > 2_000_000) return "";
  return sdp;
}

function rtcSessionView(session) {
  return {
    sessionId: session.sessionId,
    deviceId: session.deviceId,
    createdAt: session.createdAt,
    expiresAt: session.expiresAt,
    ttlSeconds: Math.max(0, Math.floor((session.expiresAt - now()) / 1000)),
    quality: session.quality,
    iceServers: session.iceServers
  };
}

function messageBuffer(raw) {
  if (Buffer.isBuffer(raw)) return raw;
  if (Array.isArray(raw)) return Buffer.concat(raw);
  if (raw instanceof ArrayBuffer) return Buffer.from(raw);
  if (ArrayBuffer.isView(raw)) return Buffer.from(raw.buffer, raw.byteOffset, raw.byteLength);
  return Buffer.from(raw);
}

function decodeBinaryFrame(raw) {
  const buffer = messageBuffer(raw);
  if (buffer.length < BINARY_FRAME_MAGIC.length + 4) {
    throw new Error("binary frame too short");
  }
  if (!buffer.subarray(0, BINARY_FRAME_MAGIC.length).equals(BINARY_FRAME_MAGIC)) {
    throw new Error("unknown binary frame");
  }
  const headerLength = buffer.readUInt32LE(BINARY_FRAME_MAGIC.length);
  if (headerLength < 2 || headerLength > BINARY_FRAME_HEADER_LIMIT) {
    throw new Error("invalid binary frame header length");
  }
  const headerStart = BINARY_FRAME_MAGIC.length + 4;
  const dataStart = headerStart + headerLength;
  if (dataStart > buffer.length) {
    throw new Error("truncated binary frame");
  }
  const header = JSON.parse(buffer.subarray(headerStart, dataStart).toString("utf8"));
  return { header, image: buffer.subarray(dataStart), byteLength: buffer.length };
}

function encodeBinaryFrame(header, image) {
  const encodedHeader = Buffer.from(JSON.stringify(header), "utf8");
  if (encodedHeader.length > BINARY_FRAME_HEADER_LIMIT) {
    throw new Error("binary frame header too large");
  }
  const payload = Buffer.allocUnsafe(BINARY_FRAME_MAGIC.length + 4 + encodedHeader.length + image.length);
  BINARY_FRAME_MAGIC.copy(payload, 0);
  payload.writeUInt32LE(encodedHeader.length, BINARY_FRAME_MAGIC.length);
  encodedHeader.copy(payload, BINARY_FRAME_MAGIC.length + 4);
  image.copy(payload, BINARY_FRAME_MAGIC.length + 4 + encodedHeader.length);
  return payload;
}

function fileSha256(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function publicBaseUrl(req) {
  const proto = String(req.get("x-forwarded-proto") || req.protocol || "https").split(",")[0].trim();
  const host = req.get("host");
  return `${proto}://${host}${PUBLIC_BASE_PATH}`;
}

function decodeHeaderValue(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function safeFileName(value) {
  const base = path.basename(decodeHeaderValue(value) || "file.bin");
  const clean = base.replace(/[\x00-\x1f<>:"/\\|?*]/g, "_").trim().slice(0, 180);
  return clean || "file.bin";
}

function transferView(transfer) {
  return {
    id: transfer.id,
    deviceId: transfer.deviceId,
    fileName: transfer.fileName,
    size: transfer.size,
    sha256: transfer.sha256,
    status: transfer.status,
    error: transfer.error || "",
    devicePath: transfer.devicePath || "",
    bytes: transfer.bytes || 0,
    createdAt: transfer.createdAt,
    updatedAt: transfer.updatedAt || transfer.createdAt,
    completedAt: transfer.completedAt || null,
    expiresAt: transfer.expiresAt
  };
}

function notifyFileTransfer(transfer) {
  const payload = { type: "file-transfer", transfer: transferView(transfer) };
  for (const ws of clients) {
    if (ws.userId === transfer.userId) {
      send(ws, payload);
    }
  }
}

function pruneFileTransfers() {
  const cutoff = now();
  for (const [id, transfer] of fileTransfers) {
    if (Number(transfer.expiresAt || 0) > cutoff) continue;
    fileTransfers.delete(id);
    if (transfer.path) {
      try {
        fs.rmSync(transfer.path, { force: true });
      } catch {
        // Best effort cleanup.
      }
    }
  }
}

function loadState() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  try {
    const parsed = JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
    return normalizeState(parsed);
  } catch (error) {
    if (error.code !== "ENOENT") {
      const backup = `${DATA_FILE}.${Date.now()}.bad`;
      try {
        fs.copyFileSync(DATA_FILE, backup);
      } catch {
        // Best effort backup only.
      }
      console.warn("state file could not be read; starting with empty state", error.message);
    }
    return normalizeState({});
  }
}

function normalizeState(raw) {
  const normalized = {
    users: Array.isArray(raw.users) ? raw.users : [],
    admins: Array.isArray(raw.admins) ? raw.admins : [],
    adminSessions: Array.isArray(raw.adminSessions) ? raw.adminSessions : [],
    sessions: Array.isArray(raw.sessions) ? raw.sessions : [],
    bindings: Array.isArray(raw.bindings) ? raw.bindings : []
  };
  const cutoff = now();
  normalized.sessions = normalized.sessions.filter((session) => Number(session.expiresAt || 0) > cutoff);
  normalized.adminSessions = normalized.adminSessions.filter((session) => Number(session.expiresAt || 0) > cutoff);
  ensureDefaultAdmin(normalized);
  ensureRemoteAdminUser(normalized);
  return normalized;
}

function ensureRemoteAdminUser(targetState = state) {
  if (targetState.users.some((user) => user.id === MAIN_ADMIN_USER_ID)) return;
  targetState.users.push({
    id: MAIN_ADMIN_USER_ID,
    username: "main-server-admin",
    usernameKey: MAIN_ADMIN_USER_ID,
    salt: "",
    passwordHash: "",
    status: "approved",
    createdAt: iso(),
    approvedAt: iso()
  });
}

function ensureDefaultAdmin(targetState = state) {
  const usernameKey = DEFAULT_ADMIN_USERNAME.toLowerCase();
  if (targetState.admins.some((admin) => admin.usernameKey === usernameKey)) return;
  const hashed = passwordHash(DEFAULT_ADMIN_PASSWORD);
  targetState.admins.push({
    id: makeId("adm"),
    username: DEFAULT_ADMIN_USERNAME,
    usernameKey,
    salt: hashed.salt,
    passwordHash: hashed.hash,
    status: "active",
    createdAt: iso()
  });
}

function saveState() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const tempFile = `${DATA_FILE}.tmp`;
  fs.writeFileSync(tempFile, JSON.stringify(state, null, 2));
  fs.renameSync(tempFile, DATA_FILE);
}

function hashToken(token) {
  return crypto.createHash("sha256").update(String(token)).digest("hex");
}

function makeId(prefix) {
  return `${prefix}_${crypto.randomBytes(12).toString("hex")}`;
}

function makeSessionToken() {
  return `bhzn_${crypto.randomBytes(32).toString("base64url")}`;
}

function passwordHash(password, salt = crypto.randomBytes(16).toString("hex")) {
  const hash = crypto.pbkdf2Sync(String(password), salt, 120_000, 32, "sha256").toString("hex");
  return { salt, hash };
}

function verifyPassword(password, user) {
  if (!user?.salt || !user?.passwordHash) return false;
  const candidate = passwordHash(password, user.salt).hash;
  const a = Buffer.from(candidate, "hex");
  const b = Buffer.from(user.passwordHash, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function publicUser(user) {
  return {
    id: user.id,
    username: user.username,
    status: user.status,
    createdAt: user.createdAt,
    approvedAt: user.approvedAt || null
  };
}

function adminUser(user) {
  return {
    ...publicUser(user),
    rejectedAt: user.rejectedAt || null,
    lastLoginAt: user.lastLoginAt || null
  };
}

function normalizeUsername(username) {
  return String(username || "").trim();
}

function normalizeDeviceId(id) {
  return String(id || "").trim().toUpperCase();
}

function normalizeDeviceCode(code) {
  return String(code || "").replace(/\D/g, "").slice(0, 8);
}

function normalizePlatform(platform) {
  const value = String(platform || "android").trim().toLowerCase();
  if (["android", "windows", "macos", "ios", "linux"].includes(value)) return value;
  return "android";
}

function defaultDeviceName(platform) {
  if (platform === "windows") return "BHZN Windows";
  if (platform === "macos") return "BHZN Mac";
  if (platform === "linux") return "BHZN Linux";
  if (platform === "ios") return "BHZN iPhone";
  return "BHZN Android";
}

function hasScreenPermission(device) {
  return Boolean(device?.permissions?.mediaProjection || device?.permissions?.screenCapture);
}

function hasInputPermission(device) {
  const permissions = device?.permissions || {};
  if (Object.prototype.hasOwnProperty.call(permissions, "inputControl")) {
    return Boolean(permissions.inputControl);
  }
  if ((device?.platform || "android") === "android") return false;
  return Boolean(permissions.accessibility);
}

function bearerToken(req) {
  const auth = String(req.get("authorization") || "");
  if (auth.toLowerCase().startsWith("bearer ")) {
    return auth.slice(7).trim();
  }
  return String(req.query.session || req.get("x-session-token") || "");
}

function adminToken(req) {
  const auth = String(req.get("authorization") || "");
  if (auth.toLowerCase().startsWith("bearer ")) {
    return auth.slice(7).trim();
  }
  return String(req.get("x-admin-token") || req.query.token || "");
}

function isAdminToken(token) {
  return Boolean(ADMIN_TOKEN && token === ADMIN_TOKEN);
}

function adminFromToken(token) {
  if (!token) return null;
  if (isAdminToken(token)) {
    return { admin: { id: "legacy-token", username: "token-admin", status: "active" }, session: null };
  }
  const tokenHash = hashToken(token);
  const session = state.adminSessions.find((item) => item.tokenHash === tokenHash);
  if (!session || Number(session.expiresAt || 0) <= now()) return null;
  const admin = state.admins.find((item) => item.id === session.adminId);
  if (!admin || admin.status !== "active") return null;
  session.lastSeen = iso();
  return { admin, session };
}

function sessionFromToken(token) {
  if (!token) return null;
  const tokenHash = hashToken(token);
  const session = state.sessions.find((item) => item.tokenHash === tokenHash);
  if (!session || Number(session.expiresAt || 0) <= now()) return null;
  const user = state.users.find((item) => item.id === session.userId);
  if (!user) return null;
  session.lastSeen = iso();
  return { session, user };
}

function requireSession(req, res, next) {
  const auth = sessionFromToken(bearerToken(req));
  if (!auth) {
    res.status(401).json({ error: "unauthorized" });
    return;
  }
  req.auth = auth;
  next();
}

function requireApprovedUser(req, res, next) {
  requireSession(req, res, () => {
    if (req.auth.user.status !== "approved") {
      res.status(403).json({ error: "user_not_approved", user: publicUser(req.auth.user) });
      return;
    }
    next();
  });
}

function requireAdmin(req, res, next) {
  const auth = adminFromToken(adminToken(req));
  if (auth) {
    req.adminAuth = auth;
    next();
    return;
  }
  res.status(401).json({ error: "admin_unauthorized" });
}

async function verifyMainAdmin(req) {
  const token = adminToken(req);
  if (!token || !REMOTE_SERVICE_KEY) return null;
  try {
    const response = await fetch(`${MAIN_API_BASE}/admin/api/remote-support/controller-auth`, {
      headers: {
        "X-Admin-Token": token,
        "X-Remote-Service-Key": REMOTE_SERVICE_KEY
      }
    });
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    console.warn("main admin verification failed", error?.message || String(error));
    return null;
  }
}

async function requireMainAdmin(req, res, next) {
  const admin = await verifyMainAdmin(req);
  if (!admin) {
    res.status(401).json({ error: "main_admin_unauthorized" });
    return;
  }
  req.mainAdmin = admin;
  next();
}

function issueSession(user, ttlMs = SESSION_TTL_MS) {
  const token = makeSessionToken();
  state.sessions.push({
    tokenHash: hashToken(token),
    userId: user.id,
    createdAt: iso(),
    lastSeen: iso(),
    expiresAt: now() + Math.max(60_000, Number(ttlMs) || SESSION_TTL_MS)
  });
  user.lastLoginAt = iso();
  saveState();
  return token;
}

function issueAdminSession(admin) {
  const token = makeSessionToken();
  state.adminSessions.push({
    tokenHash: hashToken(token),
    adminId: admin.id,
    createdAt: iso(),
    lastSeen: iso(),
    expiresAt: now() + SESSION_TTL_MS
  });
  admin.lastLoginAt = iso();
  saveState();
  return token;
}

function deviceView(device) {
  return {
    id: device.id,
    name: device.name,
    model: device.model,
    platform: device.platform || "android",
    osVersion: device.osVersion || "",
    agentVersion: device.agentVersion || "",
    androidVersion: device.androidVersion,
    permissions: device.permissions,
    permissionDiagnostics: device.permissionDiagnostics || {},
    controlEnabled: device.controlEnabled,
    screen: device.screen,
    rtcCapabilities: publicRtcCapabilities(device),
    online: true,
    // A device is considered remote-support enabled only while its agent
    // websocket is alive.  The Online switch controls the agent process, so
    // this flag intentionally mirrors the live connection instead of a
    // stale persisted binding.
    supportEnabled: true,
    lastSeen: device.lastSeen,
    controllerCount: device.controllers.size
  };
}

function canViewDevice(device) {
  return hasScreenPermission(device);
}

function canControlDevice(device) {
  return Boolean(hasScreenPermission(device) && hasInputPermission(device));
}

function controlBlockReason(device) {
  if (!device) return "device_offline";
  if (!hasScreenPermission(device)) return "screen_not_ready";
  if (!hasInputPermission(device)) return "input_control_not_ready";
  return "";
}

function bindingView(binding) {
  const live = devices.get(binding.deviceId);
  const base = live ? deviceView(live) : {
    ...(binding.lastDevice || {}),
    id: binding.deviceId,
    online: false,
    controllerCount: 0
  };
  return {
    ...base,
    bindingId: binding.id,
    label: binding.label || base.name || defaultDeviceName(base.platform),
    monitorAlways: Boolean(binding.monitorAlways),
    addedAt: binding.createdAt,
    boundAt: binding.createdAt,
    lastSeen: live ? live.lastSeen : (binding.lastSeen || base.lastSeen || null)
  };
}

function listBoundDevices(userId) {
  pruneDevices();
  return state.bindings
    .filter((binding) => binding.userId === userId)
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    .map(bindingView);
}

function findBinding(userId, deviceId) {
  return state.bindings.find((binding) => binding.userId === userId && binding.deviceId === deviceId);
}

function updateBindingsForDevice(device, options = {}) {
  const persist = options.persist !== false;
  const owners = new Set();
  let changed = false;
  const snapshot = deviceView(device);
  for (const binding of state.bindings) {
    if (binding.deviceId !== device.id) continue;
    binding.lastSeen = device.lastSeen;
    binding.lastDevice = snapshot;
    binding.updatedAt = iso();
    owners.add(binding.userId);
    changed = true;
  }
  if (changed && persist) saveState();
  for (const userId of owners) {
    broadcastUser(userId);
  }
}

function broadcastUser(userId) {
  const devicesForUser = listBoundDevices(userId);
  for (const ws of clients) {
    if (ws.userId === userId) {
      send(ws, { type: "devices", devices: devicesForUser });
    }
  }
}

function broadcastDeviceOwners(deviceId) {
  const owners = new Set(
    state.bindings
      .filter((binding) => binding.deviceId === deviceId)
      .map((binding) => binding.userId)
  );
  for (const userId of owners) {
    broadcastUser(userId);
  }
}

function pruneDevices() {
  const cutoff = now() - DEVICE_TTL_MS;
  const removed = [];
  for (const [id, device] of devices) {
    if (device.lastSeen < cutoff || device.ws.readyState !== WebSocket.OPEN) {
      devices.delete(id);
      removed.push(id);
      for (const controller of device.controllers) {
        controllers.delete(controller);
      }
    }
  }
  return removed;
}

function makeSessionId() {
  return crypto.randomBytes(12).toString("hex");
}

function stopControllerSession(sessionId, reason, options = {}) {
  const notifyController = options.notifyController !== false;
  const notifyDevice = options.notifyDevice !== false;
  const broadcast = options.broadcast !== false;
  const controller = controllers.get(sessionId);
  if (!controller) return null;
  stopRtcSession(sessionId, reason, { notifyController, notifyDevice });
  const device = devices.get(controller.deviceId);
  controllers.delete(sessionId);
  controller.ws.sessions.delete(sessionId);
  if (device) {
    device.controllers.delete(sessionId);
    if (notifyDevice && device.ws.readyState === WebSocket.OPEN) {
      send(device.ws, { type: "stop-control", sessionId, reason, controllerCount: device.controllers.size });
    }
  }
  if (notifyController && controller.ws.readyState === WebSocket.OPEN) {
    send(controller.ws, { type: "control-stopped", sessionId, reason });
  }
  relayLog("control.stop", {
    sessionId,
    reason,
    userId: controller.userId,
    deviceId: controller.deviceId,
    controllerCount: device ? device.controllers.size : 0
  });
  if (broadcast && device) broadcastDeviceOwners(device.id);
  return controller;
}

function stopRtcSession(sessionId, reason, options = {}) {
  const session = rtcSessions.get(sessionId);
  if (!session) return null;
  rtcSessions.delete(sessionId);
  const payload = {
    type: "rtc-stopped",
    sessionId,
    deviceId: session.deviceId,
    reason: String(reason || "stopped")
  };
  if (options.notifyController !== false && session.controllerWs?.readyState === WebSocket.OPEN) {
    send(session.controllerWs, payload);
  }
  if (options.notifyDevice !== false && session.deviceWs?.readyState === WebSocket.OPEN) {
    send(session.deviceWs, payload);
  }
  relayLog("rtc.stop", {
    sessionId,
    reason: payload.reason,
    userId: session.userId,
    deviceId: session.deviceId
  });
  return session;
}

function stopRtcSessionsForWs(ws, reason) {
  for (const [sessionId, session] of Array.from(rtcSessions)) {
    if (session.controllerWs === ws || session.deviceWs === ws) {
      stopRtcSession(sessionId, reason, {
        notifyController: session.controllerWs !== ws,
        notifyDevice: session.deviceWs !== ws
      });
    }
  }
}

function pruneRtcSessions() {
  const cutoff = now();
  for (const [sessionId, session] of Array.from(rtcSessions)) {
    if (session.expiresAt <= cutoff || session.controllerWs?.readyState !== WebSocket.OPEN || session.deviceWs?.readyState !== WebSocket.OPEN) {
      stopRtcSession(sessionId, session.expiresAt <= cutoff ? "expired" : "peer_closed");
    }
  }
}

function pruneRelaySessions() {
  const cutoff = now();
  for (const [sessionId, controller] of Array.from(controllers)) {
    if (controller.expiresAt && controller.expiresAt <= cutoff) {
      stopControllerSession(sessionId, "relay_time_limit", {
        notifyController: true,
        notifyDevice: true,
        broadcast: true
      });
    }
  }
}

function rtcSessionForMessage(ws, msg) {
  const sessionId = String(msg.sessionId || "");
  const session = rtcSessions.get(sessionId);
  if (!session) {
    send(ws, { type: "error", error: "bad_rtc_session", sessionId });
    return null;
  }
  if (ws.role === "controller" && (session.controllerWs !== ws || session.userId !== ws.userId)) {
    send(ws, { type: "error", error: "bad_rtc_session", sessionId });
    return null;
  }
  if (ws.role === "device" && (session.deviceWs !== ws || session.deviceId !== ws.deviceId)) {
    send(ws, { type: "error", error: "bad_rtc_session", sessionId });
    return null;
  }
  return session;
}

function startRtcSession(ws, msg) {
  const id = normalizeDeviceId(msg.deviceId);
  const binding = findBinding(ws.userId, id);
  const device = devices.get(id);
  if (!binding) {
    send(ws, { type: "error", error: "device_not_bound", deviceId: id });
    return;
  }
  if (!device) {
    send(ws, { type: "error", error: "device_offline", device: bindingView(binding), deviceId: id });
    return;
  }
  if (!canViewDevice(device)) {
    send(ws, { type: "error", error: "screen_not_ready", device: deviceView(device), deviceId: id });
    return;
  }
  const rtcCapabilities = publicRtcCapabilities(device);
  if (!rtcCapabilities.webrtc || !rtcCapabilities.video) {
    send(ws, { type: "error", error: "rtc_not_supported", device: deviceView(device), deviceId: id });
    return;
  }
  const mode = sanitizeSessionMode(msg.mode);
  const quality = qualityProfileFromValue(msg.quality || msg.qualityProfile, "balanced");
  for (const [existingSessionId, existingSession] of Array.from(rtcSessions)) {
    if (existingSession.controllerWs === ws && existingSession.deviceId === id) {
      if (existingSession.mode === mode && existingSession.quality?.profile === quality.profile) {
        existingSession.expiresAt = now() + RTC_SESSION_TTL_MS;
        existingSession.updatedAt = now();
        send(ws, { type: "rtc-ready", ...rtcSessionView(existingSession), device: deviceView(device) });
        return;
      }
      stopRtcSession(existingSessionId, "replaced");
    }
  }
  const sessionId = makeSessionId();
  const iceServers = rtcIceServers(ws.userId, id, sessionId);
  const session = {
    sessionId,
    userId: ws.userId,
    deviceId: id,
    controllerWs: ws,
    deviceWs: device.ws,
    iceServers,
    createdAt: now(),
    expiresAt: now() + RTC_SESSION_TTL_MS,
    mode,
    quality,
    state: "created",
    selectedCandidateType: "unknown",
    rttMs: 0,
    bitrateKbps: 0,
    bytesSent: 0,
    bytesReceived: 0,
    packetsLost: 0,
    updatedAt: now()
  };
  rtcSessions.set(sessionId, session);
  const view = rtcSessionView(session);
  send(ws, { type: "rtc-ready", ...view, device: deviceView(device) });
  send(device.ws, {
    type: "rtc-request",
    ...view,
    controllerId: ws.userId,
    mode: session.mode,
    quality
  });
  relayLog("rtc.ready", {
    sessionId,
    userId: ws.userId,
    deviceId: id,
    quality: quality.profile,
    iceServerCount: iceServers.length,
    hasTurn: iceServers.some((item) => String([].concat(item.urls || []).join(",")).includes("turn:"))
  });
}

function forwardRtcSignal(ws, msg) {
  const session = rtcSessionForMessage(ws, msg);
  if (!session) return;
  const fromController = ws.role === "controller";
  const target = fromController ? session.deviceWs : session.controllerWs;
  if (!target || target.readyState !== WebSocket.OPEN) {
    send(ws, { type: "error", error: "rtc_peer_offline", sessionId: session.sessionId });
    return;
  }
  const base = {
    type: msg.type,
    sessionId: session.sessionId,
    deviceId: session.deviceId
  };
  if (msg.type === "rtc-offer" || msg.type === "rtc-answer") {
    const sdp = sanitizeRtcSdp(msg.sdp);
    if (!sdp) {
      send(ws, { type: "error", error: "bad_rtc_sdp", sessionId: session.sessionId });
      return;
    }
    send(target, { ...base, sdp });
    return;
  }
  if (msg.type === "rtc-ice-candidate") {
    const candidate = sanitizeRtcCandidate(msg.candidate);
    if (!candidate) return;
    send(target, { ...base, candidate });
    return;
  }
  if (msg.type === "rtc-state") {
    const state = String(msg.state || "").slice(0, 64);
    const selectedCandidateType = String(msg.selectedCandidateType || "unknown").slice(0, 32);
    const rttMs = Math.max(0, Number(msg.rttMs || 0));
    const bitrateKbps = Math.max(0, Number(msg.bitrateKbps || 0));
    const bytesSent = Math.max(0, Number(msg.bytesSent || 0));
    const bytesReceived = Math.max(0, Number(msg.bytesReceived || 0));
    const packetsLost = Math.max(0, Number(msg.packetsLost || 0));
    session.state = state;
    session.selectedCandidateType = selectedCandidateType;
    session.rttMs = rttMs;
    session.bitrateKbps = bitrateKbps;
    session.bytesSent = bytesSent;
    session.bytesReceived = bytesReceived;
    session.packetsLost = packetsLost;
    session.updatedAt = now();
    const stats = trafficStatsFor(session.deviceId);
    stats.lastRtcState = state;
    stats.lastRtcCandidateType = selectedCandidateType;
    stats.rtcRttMs = rttMs;
    stats.rtcBitrateKbps = bitrateKbps;
    stats.rtcBytesSent = Math.max(Number(stats.rtcBytesSent || 0), bytesSent);
    stats.rtcBytesReceived = Math.max(Number(stats.rtcBytesReceived || 0), bytesReceived);
    stats.rtcPacketsLost = Math.max(Number(stats.rtcPacketsLost || 0), packetsLost);
    stats.lastRtcUpdatedAt = iso();
    relayLog("rtc.state", {
      sessionId: session.sessionId,
      userId: session.userId,
      deviceId: session.deviceId,
      role: ws.role,
      state,
      selectedCandidateType,
      rttMs,
      bitrateKbps,
      bytesSent,
      bytesReceived,
      packetsLost
    });
    send(target, {
      ...base,
      state,
      selectedCandidateType,
      rttMs,
      bitrateKbps,
      bytesSent,
      bytesReceived,
      packetsLost
    });
    return;
  }
  if (msg.type === "rtc-stop") {
    stopRtcSession(session.sessionId, msg.reason || "stopped");
  }
}

function attachController(device, ws, options = {}) {
  if (supportsRtcRelayBypass(device) && !ws.allowRtcRelayFallback) {
    send(ws, { type: "error", error: "rtc_required", device: deviceView(device), deviceId: device.id });
    return;
  }
  ws.allowRtcRelayFallback = false;
  const mode = sanitizeSessionMode(options.mode);
  const reason = String(options.reason || "").slice(0, 120);
  let replaced = 0;
  for (const existingSessionId of Array.from(ws.sessions)) {
    const existing = controllers.get(existingSessionId);
    if (existing?.deviceId === device.id) {
      stopControllerSession(existingSessionId, "replaced_by_new_session", {
        notifyController: false,
        notifyDevice: true,
        broadcast: false
      });
      replaced += 1;
    }
  }
  const sessionId = makeSessionId();
  const relayQuality = { ...RELAY_QUALITY_PROFILE };
  const expiresAt = now() + RELAY_SESSION_TTL_MS;
  controllers.set(sessionId, {
    sessionId,
    ws,
    userId: ws.userId,
    deviceId: device.id,
    createdAt: now(),
    expiresAt,
    mode,
    reason,
    quality: relayQuality
  });
  device.controllers.add(sessionId);
  ws.sessions.add(sessionId);
  relayLog("control.ready", {
    sessionId,
    userId: ws.userId,
    deviceId: device.id,
    mode,
    reason,
    replaced,
    ttlSeconds: Math.floor(RELAY_SESSION_TTL_MS / 1000),
    controllerCount: device.controllers.size
  });
  send(ws, {
    type: "control-ready",
    sessionId,
    device: deviceView(device),
    path: "ws-relay",
    mode,
    quality: relayQuality,
    expiresAt,
    ttlSeconds: Math.floor(RELAY_SESSION_TTL_MS / 1000)
  });
  send(device.ws, {
    type: "control-request",
    sessionId,
    mode,
    controllerCount: device.controllers.size,
    quality: relayQuality,
    relayTtlSeconds: Math.floor(RELAY_SESSION_TTL_MS / 1000)
  });
  broadcastDeviceOwners(device.id);
}

function handleBinaryDeviceFrame(ws, raw) {
  if (ws.role !== "device" || !ws.deviceId) {
    send(ws, { type: "error", error: "bad_binary_role" });
    return;
  }
  const device = devices.get(ws.deviceId);
  if (!device) return;
  if (hasActiveRtcSessionForDevice(ws.deviceId)) {
    device.lastSeen = now();
    return;
  }
  let frame;
  try {
    frame = decodeBinaryFrame(raw);
  } catch (error) {
    console.warn("drop invalid binary frame", {
      deviceId: ws.deviceId,
      message: error?.message || String(error)
    });
    return;
  }
  const header = frame.header || {};
  const frameKind = String(header.frameKind || "jpeg");
  if (frameKind !== "jpeg") return;
  const width = Math.max(0, Number(header.width || 0));
  const height = Math.max(0, Number(header.height || 0));
  const inputWidth = Math.max(0, Number(header.inputWidth || width));
  const inputHeight = Math.max(0, Number(header.inputHeight || height));
  device.lastSeen = now();
  if (width && height) {
    device.screen = { width, height, inputWidth, inputHeight };
  }
  const forwarded = encodeBinaryFrame({
    type: "frame",
    deviceId: ws.deviceId,
    frameId: header.frameId || header.timestamp || now(),
    frameKind,
    width,
    height,
    inputWidth,
    inputHeight,
    timestamp: header.timestamp || now()
  }, frame.image);
  let delivered = 0;
  for (const sessionId of device.controllers) {
    const controller = controllers.get(sessionId);
    if (controller && sendRealtimeBinaryFrame(controller.ws, forwarded)) delivered += 1;
  }
  addFrameTraffic(ws.deviceId, frame.byteLength || messageBuffer(raw).length, forwarded.length * delivered, delivered);
}

app.get("/api/health", (req, res) => {
  res.json({ ok: true, time: new Date().toISOString(), port: PORT, mainAuthOnly: !ALLOW_SELF_SERVICE });
});

app.get("/api/releases/windows-agent", (req, res) => {
  const filePath = path.join(PUBLIC_DIR, "downloads", WINDOWS_AGENT_FILE);
  if (!fs.existsSync(filePath)) {
    res.status(404).json({ error: "release_not_found" });
    return;
  }
  const stat = fs.statSync(filePath);
  res.json({
    platform: "windows",
    version: WINDOWS_AGENT_VERSION,
    url: `${publicBaseUrl(req)}/downloads/${encodeURIComponent(WINDOWS_AGENT_FILE)}`,
    sha256: fileSha256(filePath),
    size: stat.size,
    updatedAt: stat.mtime.toISOString()
  });
});

app.get("/api/releases/android-apk", (req, res) => {
  const filePath = path.join(PUBLIC_DIR, "downloads", ANDROID_APK_FILE);
  if (!fs.existsSync(filePath)) {
    res.status(404).json({ error: "release_not_found" });
    return;
  }
  const stat = fs.statSync(filePath);
  res.json({
    platform: "android",
    version: ANDROID_APK_VERSION,
    versionCode: ANDROID_APK_VERSION_CODE,
    url: `${publicBaseUrl(req)}/downloads/${encodeURIComponent(ANDROID_APK_FILE)}`,
    sha256: fileSha256(filePath),
    size: stat.size,
    updatedAt: stat.mtime.toISOString()
  });
});

function releaseFileView(req, platform, arch, version, fileName) {
  const filePath = path.join(PUBLIC_DIR, "downloads", fileName);
  if (!fs.existsSync(filePath)) return null;
  const stat = fs.statSync(filePath);
  return {
    platform,
    arch,
    version,
    url: `${publicBaseUrl(req)}/downloads/${encodeURIComponent(fileName)}`,
    sha256: fileSha256(filePath),
    size: stat.size,
    updatedAt: stat.mtime.toISOString()
  };
}

app.get("/api/releases/macos-agent", (req, res) => {
  const releases = [
    releaseFileView(req, "macos", "arm64", MAC_AGENT_ARM64_VERSION, MAC_AGENT_ARM64_FILE),
    releaseFileView(req, "macos", "x86_64", MAC_AGENT_X86_64_VERSION, MAC_AGENT_X86_64_FILE)
  ].filter(Boolean);
  if (!releases.length) {
    res.status(404).json({ error: "release_not_found" });
    return;
  }
  res.json({ platform: "macos", version: MAC_AGENT_ARM64_VERSION, releases });
});

app.post("/api/auth/register", (req, res) => {
  if (!ALLOW_SELF_SERVICE) {
    res.status(404).json({ error: "self_service_disabled" });
    return;
  }
  const username = normalizeUsername(req.body.username);
  const password = String(req.body.password || "");
  if (username.length < 2 || username.length > 64 || password.length < 6) {
    res.status(400).json({ error: "bad_credentials" });
    return;
  }
  const usernameKey = username.toLowerCase();
  if (state.users.some((user) => user.usernameKey === usernameKey)) {
    res.status(409).json({ error: "username_exists" });
    return;
  }
  const hashed = passwordHash(password);
  const user = {
    id: makeId("usr"),
    username,
    usernameKey,
    salt: hashed.salt,
    passwordHash: hashed.hash,
    status: "pending",
    createdAt: iso()
  };
  state.users.push(user);
  saveState();
  res.status(201).json({ ok: true, user: publicUser(user) });
});

app.post("/api/auth/login", (req, res) => {
  if (!ALLOW_SELF_SERVICE) {
    res.status(404).json({ error: "self_service_disabled" });
    return;
  }
  const usernameKey = normalizeUsername(req.body.username).toLowerCase();
  const password = String(req.body.password || "");
  const user = state.users.find((item) => item.usernameKey === usernameKey);
  if (!user || !verifyPassword(password, user)) {
    res.status(401).json({ error: "invalid_login" });
    return;
  }
  const token = issueSession(user);
  res.json({ ok: true, token, user: publicUser(user) });
});

app.get("/api/me", requireSession, (req, res) => {
  res.json({ user: publicUser(req.auth.user) });
});

app.post("/api/auth/logout", requireSession, (req, res) => {
  state.sessions = state.sessions.filter((session) => session !== req.auth.session);
  saveState();
  res.json({ ok: true });
});

app.post("/api/admin/login", (req, res) => {
  if (!ALLOW_SELF_SERVICE) {
    res.status(404).json({ error: "self_service_disabled" });
    return;
  }
  const usernameKey = normalizeUsername((req.body || {}).username).toLowerCase();
  const password = String((req.body || {}).password || "");
  const admin = state.admins.find((item) => item.usernameKey === usernameKey && item.status === "active");
  if (!admin || !verifyPassword(password, admin)) {
    res.status(401).json({ error: "invalid_admin_login" });
    return;
  }
  const token = issueAdminSession(admin);
  res.json({ ok: true, token, admin: { id: admin.id, username: admin.username } });
});

app.get("/api/admin/me", requireAdmin, (req, res) => {
  res.json({ admin: { id: req.adminAuth.admin.id, username: req.adminAuth.admin.username } });
});

app.post("/api/admin/logout", requireAdmin, (req, res) => {
  if (req.adminAuth.session) {
    state.adminSessions = state.adminSessions.filter((session) => session !== req.adminAuth.session);
    saveState();
  }
  res.json({ ok: true });
});

// Main Lobster admin integration.  These are intentionally separate from the
// old self-service APIs below: only a valid main-server admin token may add a
// machine, issue a controller session, or view the monitoring wall.
app.post("/api/remote-admin/controller-session", requireMainAdmin, (req, res) => {
  const user = state.users.find((item) => item.id === MAIN_ADMIN_USER_ID);
  if (!user) {
    res.status(500).json({ error: "remote_admin_user_missing" });
    return;
  }
  const token = issueSession(user, RELAY_SESSION_TTL_MS);
  res.json({ ok: true, token, user: publicUser(user), expiresAt: iso(now() + RELAY_SESSION_TTL_MS), ttlSeconds: Math.floor(RELAY_SESSION_TTL_MS / 1000) });
});

app.get("/api/remote-admin/devices", requireMainAdmin, (req, res) => {
  pruneDevices();
  const devices = listBoundDevices(MAIN_ADMIN_USER_ID).map((device) => {
    const stats = trafficStatsView(trafficStatsFor(device.id));
    return {
      ...device,
      activePath: stats.activePath,
      traffic: stats,
      rtcSessions: stats.activeRtcSessions || [],
      relaySessions: stats.activeRelaySessions || []
    };
  });
  res.json({ devices, updatedAt: iso() });
});

app.post("/api/remote-admin/devices", requireMainAdmin, (req, res) => {
  const body = req.body || {};
  const deviceId = normalizeDeviceId(body.deviceId);
  const verificationCode = normalizeDeviceCode(body.verificationCode);
  if (!/^[A-Z0-9-]{6,32}$/.test(deviceId) || verificationCode.length < 4) {
    res.status(400).json({ error: "bad_device_or_code" });
    return;
  }
  const device = devices.get(deviceId);
  if (!device || device.ws.readyState !== WebSocket.OPEN) {
    res.status(404).json({ error: "device_offline" });
    return;
  }
  if (!device.verificationCode || device.verificationCode !== verificationCode) {
    res.status(403).json({ error: "bad_verification_code" });
    return;
  }
  let binding = findBinding(MAIN_ADMIN_USER_ID, deviceId);
  if (!binding) {
    binding = {
      id: makeId("admin_bind"),
      userId: MAIN_ADMIN_USER_ID,
      deviceId,
      label: String(body.label || "").trim().slice(0, 80) || device.name || defaultDeviceName(device.platform),
      monitorAlways: Boolean(body.monitorAlways),
      createdAt: iso()
    };
    state.bindings.push(binding);
  } else {
    if (Object.prototype.hasOwnProperty.call(body, "label")) {
      binding.label = String(body.label || "").trim().slice(0, 80) || binding.label;
    }
    if (Object.prototype.hasOwnProperty.call(body, "monitorAlways")) binding.monitorAlways = Boolean(body.monitorAlways);
  }
  binding.lastSeen = device.lastSeen;
  binding.lastDevice = deviceView(device);
  binding.updatedAt = iso();
  saveState();
  broadcastUser(MAIN_ADMIN_USER_ID);
  res.status(201).json({ ok: true, device: bindingView(binding) });
});

app.patch("/api/remote-admin/devices/:id", requireMainAdmin, (req, res) => {
  const deviceId = normalizeDeviceId(req.params.id);
  const binding = findBinding(MAIN_ADMIN_USER_ID, deviceId);
  if (!binding) {
    res.status(404).json({ error: "binding_not_found" });
    return;
  }
  const body = req.body || {};
  if (Object.prototype.hasOwnProperty.call(body, "label")) {
    binding.label = String(body.label || "").trim().slice(0, 80) || binding.label;
  }
  if (Object.prototype.hasOwnProperty.call(body, "monitorAlways")) binding.monitorAlways = Boolean(body.monitorAlways);
  binding.updatedAt = iso();
  saveState();
  broadcastUser(MAIN_ADMIN_USER_ID);
  res.json({ ok: true, device: bindingView(binding) });
});

app.delete("/api/remote-admin/devices/:id", requireMainAdmin, (req, res) => {
  const deviceId = normalizeDeviceId(req.params.id);
  const binding = findBinding(MAIN_ADMIN_USER_ID, deviceId);
  if (!binding) {
    res.status(404).json({ error: "binding_not_found" });
    return;
  }
  state.bindings = state.bindings.filter((item) => item !== binding);
  for (const [sessionId, controller] of Array.from(controllers)) {
    if (controller.userId === MAIN_ADMIN_USER_ID && controller.deviceId === deviceId) {
      stopControllerSession(sessionId, "admin_binding_deleted", { broadcast: false });
    }
  }
  saveState();
  broadcastUser(MAIN_ADMIN_USER_ID);
  res.json({ ok: true });
});

app.get("/api/devices", requireApprovedUser, (req, res) => {
  res.json({ devices: listBoundDevices(req.auth.user.id) });
});

app.post("/api/bindings", requireApprovedUser, (req, res) => {
  if (!ALLOW_SELF_SERVICE) {
    res.status(403).json({ error: "admin_binding_required" });
    return;
  }
  const deviceId = normalizeDeviceId(req.body.deviceId);
  const verificationCode = normalizeDeviceCode(req.body.verificationCode);
  if (!/^[A-Z0-9-]{6,32}$/.test(deviceId) || verificationCode.length < 4) {
    res.status(400).json({ error: "bad_device_or_code" });
    return;
  }
  const device = devices.get(deviceId);
  if (!device || device.ws.readyState !== WebSocket.OPEN) {
    res.status(404).json({ error: "device_offline" });
    return;
  }
  if (!device.verificationCode || device.verificationCode !== verificationCode) {
    res.status(403).json({ error: "bad_verification_code" });
    return;
  }
  let binding = findBinding(req.auth.user.id, deviceId);
  if (!binding) {
    binding = {
      id: makeId("bind"),
      userId: req.auth.user.id,
      deviceId,
      label: device.name || "Android Device",
      monitorAlways: false,
      createdAt: iso()
    };
    state.bindings.push(binding);
  }
  binding.lastSeen = device.lastSeen;
  binding.lastDevice = deviceView(device);
  binding.updatedAt = iso();
  saveState();
  broadcastUser(req.auth.user.id);
  res.json({ ok: true, device: bindingView(binding), devices: listBoundDevices(req.auth.user.id) });
});

app.delete("/api/bindings/:id", requireApprovedUser, (req, res) => {
  const binding = state.bindings.find((item) => item.userId === req.auth.user.id && item.id === req.params.id);
  if (!binding) {
    res.status(404).json({ error: "binding_not_found" });
    return;
  }
  state.bindings = state.bindings.filter((item) => item !== binding);
  for (const [sessionId, controller] of Array.from(controllers)) {
    if (controller.userId !== req.auth.user.id || controller.deviceId !== binding.deviceId) continue;
    stopControllerSession(sessionId, "binding_deleted", {
      notifyController: true,
      notifyDevice: true,
      broadcast: false
    });
  }
  saveState();
  broadcastUser(req.auth.user.id);
  broadcastDeviceOwners(binding.deviceId);
  res.json({ ok: true, devices: listBoundDevices(req.auth.user.id) });
});

app.patch("/api/bindings/:id", requireApprovedUser, (req, res) => {
  const binding = state.bindings.find((item) => item.userId === req.auth.user.id && item.id === req.params.id);
  if (!binding) {
    res.status(404).json({ error: "binding_not_found" });
    return;
  }
  const body = req.body || {};
  if (Object.prototype.hasOwnProperty.call(body, "label")) {
    const label = String(body.label || "").trim().slice(0, 40);
    binding.label = label || (binding.lastDevice?.name || defaultDeviceName(binding.lastDevice?.platform));
  }
  if (Object.prototype.hasOwnProperty.call(body, "monitorAlways")) {
    binding.monitorAlways = Boolean(body.monitorAlways);
  }
  binding.updatedAt = iso();
  saveState();
  broadcastUser(req.auth.user.id);
  res.json({ ok: true, device: bindingView(binding), devices: listBoundDevices(req.auth.user.id) });
});

app.post("/api/control/:id", requireApprovedUser, (req, res) => {
  const deviceId = normalizeDeviceId(req.params.id);
  const binding = findBinding(req.auth.user.id, deviceId);
  const device = devices.get(deviceId);
  if (!binding) {
    res.status(403).json({ error: "device_not_bound" });
    return;
  }
  if (!device) {
    res.status(404).json({ error: "device_offline", device: bindingView(binding) });
    return;
  }
  res.json({ ok: true, device: deviceView(device) });
});

app.get("/api/file-transfers", requireApprovedUser, (req, res) => {
  pruneFileTransfers();
  const transfers = Array.from(fileTransfers.values())
    .filter((transfer) => transfer.userId === req.auth.user.id)
    .sort((a, b) => String(b.updatedAt || b.createdAt).localeCompare(String(a.updatedAt || a.createdAt)))
    .slice(0, 100)
    .map(transferView);
  res.json({ transfers });
});

app.post("/api/file-transfers", requireApprovedUser, express.raw({
  type: "application/octet-stream",
  limit: FILE_TRANSFER_MAX_BYTES
}), (req, res) => {
  pruneFileTransfers();
  const deviceId = normalizeDeviceId(req.get("x-device-id"));
  const binding = findBinding(req.auth.user.id, deviceId);
  const device = devices.get(deviceId);
  if (!binding) {
    res.status(403).json({ error: "device_not_bound" });
    return;
  }
  if (!device || device.ws.readyState !== WebSocket.OPEN) {
    res.status(404).json({ error: "device_offline", device: bindingView(binding) });
    return;
  }
  if (!Buffer.isBuffer(req.body) || req.body.length < 1) {
    res.status(400).json({ error: "empty_file" });
    return;
  }
  if (req.body.length > FILE_TRANSFER_MAX_BYTES) {
    res.status(413).json({ error: "file_too_large" });
    return;
  }

  const transferId = makeId("file");
  const token = crypto.randomBytes(32).toString("base64url");
  const fileName = safeFileName(req.get("x-file-name"));
  const sha256 = crypto.createHash("sha256").update(req.body).digest("hex");
  const dir = path.join(FILE_TRANSFER_DIR, transferId);
  const filePath = path.join(dir, fileName);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(filePath, req.body);

  const transfer = {
    id: transferId,
    userId: req.auth.user.id,
    deviceId,
    tokenHash: hashToken(token),
    fileName,
    size: req.body.length,
    sha256,
    path: filePath,
    status: "queued",
    createdAt: iso(),
    updatedAt: iso(),
    expiresAt: now() + FILE_TRANSFER_TTL_MS
  };
  fileTransfers.set(transferId, transfer);

  send(device.ws, {
    type: "file-transfer",
    transferId,
    fileName,
    size: transfer.size,
    sha256,
    url: `${publicBaseUrl(req)}/api/file-transfers/${encodeURIComponent(transferId)}/download?token=${encodeURIComponent(token)}`
  });
  transfer.status = "dispatched";
  transfer.updatedAt = iso();
  notifyFileTransfer(transfer);
  relayLog("file.transfer.dispatched", {
    transferId,
    userId: req.auth.user.id,
    deviceId,
    size: transfer.size,
    fileName
  });
  res.status(201).json({ ok: true, transfer: transferView(transfer) });
});

app.get("/api/file-transfers/:id/download", (req, res) => {
  pruneFileTransfers();
  const transfer = fileTransfers.get(String(req.params.id || ""));
  const token = String(req.query.token || "");
  if (!transfer || !token || transfer.tokenHash !== hashToken(token)) {
    res.status(404).json({ error: "transfer_not_found" });
    return;
  }
  if (!fs.existsSync(transfer.path)) {
    res.status(410).json({ error: "transfer_expired" });
    return;
  }
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("Content-Type", "application/octet-stream");
  res.setHeader("Content-Length", String(transfer.size));
  res.setHeader("Content-Disposition", `attachment; filename*=UTF-8''${encodeURIComponent(transfer.fileName)}`);
  fs.createReadStream(transfer.path).pipe(res);
});

app.get("/api/admin/users", requireAdmin, (req, res) => {
  res.json({
    users: state.users
      .slice()
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
      .map(adminUser)
  });
});

app.post("/api/admin/users/:id/status", requireAdmin, (req, res) => {
  const user = state.users.find((item) => item.id === req.params.id);
  const status = String((req.body || {}).status || "");
  if (!user || !["pending", "approved", "rejected"].includes(status)) {
    res.status(400).json({ error: "bad_user_or_status" });
    return;
  }
  user.status = status;
  if (status === "approved") {
    user.approvedAt = iso();
    delete user.rejectedAt;
  }
  if (status === "rejected") {
    user.rejectedAt = iso();
  }
  saveState();
  res.json({ ok: true, user: adminUser(user) });
});

app.get("/api/admin/bindings", requireAdmin, (req, res) => {
  res.json({
    bindings: state.bindings
      .slice()
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
      .map((binding) => {
        const user = state.users.find((item) => item.id === binding.userId);
        return {
          ...bindingView(binding),
          userId: binding.userId,
          username: user?.username || "unknown"
        };
      })
  });
});

app.get("/api/admin/traffic", requireAdmin, (req, res) => {
  const deviceId = normalizeDeviceId(req.query.deviceId);
  const stats = deviceId
    ? [trafficStatsView(trafficStatsFor(deviceId))]
    : Array.from(trafficStats.values()).map(trafficStatsView);
  res.json({ stats });
});

app.get("/api/admin/device-links", requireAdmin, (req, res) => {
  const deviceIds = new Set([
    ...state.bindings.map((binding) => binding.deviceId),
    ...devices.keys(),
    ...trafficStats.keys()
  ]);
  const links = Array.from(deviceIds)
    .sort()
    .map((deviceId) => {
      const live = devices.get(deviceId);
      const bindings = state.bindings.filter((binding) => binding.deviceId === deviceId);
      const stats = trafficStatsView(trafficStatsFor(deviceId));
      return {
        deviceId,
        name: live?.name || bindings[0]?.lastDevice?.name || "",
        label: bindings[0]?.label || live?.name || bindings[0]?.lastDevice?.name || defaultDeviceName(live?.platform || bindings[0]?.lastDevice?.platform),
        platform: live?.platform || bindings[0]?.lastDevice?.platform || "android",
        agentVersion: live?.agentVersion || bindings[0]?.lastDevice?.agentVersion || "",
        online: Boolean(live),
        owners: bindings.map((binding) => {
          const user = state.users.find((item) => item.id === binding.userId);
          return {
            userId: binding.userId,
            username: user?.username || "unknown",
            bindingId: binding.id,
            monitorAlways: Boolean(binding.monitorAlways)
          };
        }),
        permissions: live?.permissions || bindings[0]?.lastDevice?.permissions || {},
        screen: live?.screen || bindings[0]?.lastDevice?.screen || null,
        rtcCapabilities: publicRtcCapabilities(live || bindings[0]?.lastDevice || {}),
        link: stats
      };
    });
  res.json({ links, updatedAt: iso() });
});

// Devices with remote support explicitly enabled in Online.  Offline
// bindings are intentionally excluded: an administrator should only see
// machines that can be controlled right now.
app.get("/api/admin/remote-devices", requireAdmin, (req, res) => {
  pruneDevices();
  const remoteDevices = Array.from(devices.values())
    .filter((device) => device.ws && device.ws.readyState === WebSocket.OPEN)
    .map((device) => {
      const owners = state.bindings
        .filter((binding) => binding.deviceId === device.id)
        .map((binding) => {
          const user = state.users.find((item) => item.id === binding.userId);
          return { userId: binding.userId, username: user?.username || "unknown", bindingId: binding.id };
        });
      return { ...deviceView(device), owners };
    })
    .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  res.json({ devices: remoteDevices, updatedAt: iso() });
});

app.get("/api/admin/account-connections", requireAdmin, (req, res) => {
  const accounts = state.users
    .map(accountConnectionView)
    .sort((a, b) => {
      if (b.activeSessionCount !== a.activeSessionCount) return b.activeSessionCount - a.activeSessionCount;
      if (b.onlineDeviceCount !== a.onlineDeviceCount) return b.onlineDeviceCount - a.onlineDeviceCount;
      return a.username.localeCompare(b.username);
    });
  res.json({ accounts, updatedAt: iso() });
});

app.post("/api/admin/traffic/:deviceId/reset", requireAdmin, (req, res) => {
  const stats = resetTrafficStats(req.params.deviceId);
  res.json({ ok: true, stats: trafficStatsView(stats) });
});

wss.on("connection", (ws) => {
  ws.role = "unknown";
  ws.userId = null;
  ws.deviceId = null;
  ws.sessions = new Set();
  ws.isAlive = true;
  ws.missedPongs = 0;

  ws.on("pong", () => {
    ws.isAlive = true;
    ws.missedPongs = 0;
    if (ws.role === "device" && ws.deviceId) {
      const device = devices.get(ws.deviceId);
      if (device) device.lastSeen = now();
    }
  });

  ws.on("error", (error) => {
    console.warn("websocket error", {
      role: ws.role,
      userId: ws.userId,
      deviceId: ws.deviceId,
      code: error?.code || "",
      message: error?.message || String(error)
    });
    if (error?.code === "WS_ERR_UNSUPPORTED_MESSAGE_LENGTH") {
      try {
        ws.close(1009, "message too large");
      } catch {
        // Connection is already closing.
      }
    }
  });

  ws.on("message", (raw, isBinary) => {
    if (isBinary) {
      handleBinaryDeviceFrame(ws, raw);
      return;
    }
    let msg;
    try {
      msg = JSON.parse(String(raw));
    } catch {
      send(ws, { type: "error", error: "bad_json" });
      return;
    }

    if (msg.type === "hello-controller") {
      const auth = sessionFromToken(String(msg.token || ""));
      if (!auth || auth.user.status !== "approved") {
        send(ws, { type: "error", error: "user_not_approved" });
        ws.close(4003, "unauthorized");
        return;
      }
      ws.role = "controller";
      ws.userId = auth.user.id;
      clients.add(ws);
      send(ws, { type: "hello", role: "controller", user: publicUser(auth.user) });
      send(ws, { type: "devices", devices: listBoundDevices(auth.user.id) });
      return;
    }

    if (msg.type === "hello-device") {
      const id = normalizeDeviceId(msg.id);
      const platform = normalizePlatform(msg.platform);
      if (!/^[A-Z0-9-]{6,32}$/.test(id)) {
        send(ws, { type: "error", error: "bad_device_id" });
        return;
      }
      const previous = devices.get(id);
      if (previous && previous.ws !== ws && previous.ws.readyState === WebSocket.OPEN) {
        send(previous.ws, { type: "server-replaced" });
        previous.ws.close(4001, "device reconnected");
      }
      ws.role = "device";
      ws.deviceId = id;
      devices.set(id, {
        ws,
        id,
        name: String(msg.name || defaultDeviceName(platform)),
        model: String(msg.model || ""),
        platform,
        osVersion: String(msg.osVersion || ""),
        agentVersion: String(msg.agentVersion || ""),
        androidVersion: String(msg.androidVersion || ""),
        permissions: msg.permissions || {},
        permissionDiagnostics: msg.permissionDiagnostics || {},
        controlEnabled: Boolean(msg.controlEnabled),
        screen: msg.screen || null,
        rtcCapabilities: msg.rtcCapabilities || {},
        verificationCode: normalizeDeviceCode(msg.verificationCode),
        lastSeen: now(),
        controllers: previous?.controllers || new Set()
      });
      const device = devices.get(id);
      send(ws, { type: "hello", role: "device", id });
      updateBindingsForDevice(device);
      return;
    }

    if (ws.role === "device") {
      const device = devices.get(ws.deviceId);
      if (!device) return;
      device.lastSeen = now();

      if (msg.type === "heartbeat" || msg.type === "status") {
        if (msg.name) device.name = String(msg.name);
        if (msg.model) device.model = String(msg.model);
        if (msg.platform) device.platform = normalizePlatform(msg.platform);
        if (msg.osVersion) device.osVersion = String(msg.osVersion);
        if (msg.agentVersion) device.agentVersion = String(msg.agentVersion);
        if (msg.androidVersion) device.androidVersion = String(msg.androidVersion);
        const previousPermissions = device.permissions || {};
        const previousControlEnabled = Boolean(device.controlEnabled);
        if (msg.permissions) device.permissions = msg.permissions;
        if (msg.permissionDiagnostics) device.permissionDiagnostics = msg.permissionDiagnostics;
        if (typeof msg.controlEnabled === "boolean") device.controlEnabled = msg.controlEnabled;
        if (
          msg.type === "status"
          || previousControlEnabled !== Boolean(device.controlEnabled)
          || JSON.stringify(previousPermissions || {}) !== JSON.stringify(device.permissions || {})
        ) {
          relayLog("device.permissions", {
            deviceId: device.id,
            agentVersion: device.agentVersion || "",
            androidVersion: device.androidVersion || "",
            controlEnabled: Boolean(device.controlEnabled),
            permissions: device.permissions || {},
            permissionDiagnostics: device.permissionDiagnostics || {}
          });
        }
        if (msg.screen) device.screen = msg.screen;
        if (msg.rtcCapabilities) device.rtcCapabilities = msg.rtcCapabilities;
        if (msg.verificationCode) device.verificationCode = normalizeDeviceCode(msg.verificationCode);
        updateBindingsForDevice(device, { persist: msg.type !== "heartbeat" });
        return;
      }

      if (["rtc-answer", "rtc-ice-candidate", "rtc-state", "rtc-stop"].includes(msg.type)) {
        forwardRtcSignal(ws, msg);
        return;
      }

      if (msg.type === "frame") {
        if (hasActiveRtcSessionForDevice(ws.deviceId)) {
          device.lastSeen = now();
          return;
        }
        if (typeof msg.image !== "string" || msg.image.length > 20_000_000) {
          console.warn("drop invalid frame", {
            deviceId: ws.deviceId,
            imageLength: typeof msg.image === "string" ? msg.image.length : 0
          });
          return;
        }
        const fromDeviceBytes = Buffer.byteLength(JSON.stringify(msg));
        const payload = {
          type: "frame",
          deviceId: ws.deviceId,
          frameId: msg.frameId || msg.timestamp || now(),
          frameKind: String(msg.frameKind || "jpeg"),
          image: msg.image,
          width: msg.width,
          height: msg.height,
          timestamp: msg.timestamp || now()
        };
        const payloadBytes = Buffer.byteLength(JSON.stringify(payload));
        let delivered = 0;
        for (const sessionId of device.controllers) {
          const controller = controllers.get(sessionId);
          if (controller && sendRealtimeFrame(controller.ws, payload)) delivered += 1;
        }
        addFrameTraffic(ws.deviceId, fromDeviceBytes, payloadBytes * delivered, delivered);
        return;
      }

      if (msg.type === "control-stopped") {
        const sessionId = String(msg.sessionId || "");
        stopControllerSession(sessionId, msg.reason || "device_stopped", {
          notifyController: true,
          notifyDevice: false,
          broadcast: true
        });
        return;
      }

      if (msg.type === "file-transfer-status") {
        const transfer = fileTransfers.get(String(msg.transferId || ""));
        if (!transfer || transfer.deviceId !== ws.deviceId) return;
        const status = String(msg.status || "");
        if (!["downloading", "saved", "failed"].includes(status)) return;
        transfer.status = status;
        transfer.updatedAt = iso();
        transfer.error = String(msg.error || "").slice(0, 400);
        transfer.devicePath = String(msg.path || "").slice(0, 500);
        transfer.bytes = Math.max(0, Number(msg.bytes || 0));
        if (status === "saved" || status === "failed") {
          transfer.completedAt = iso();
        }
        notifyFileTransfer(transfer);
        relayLog("file.transfer.status", {
          transferId: transfer.id,
          deviceId: ws.deviceId,
          status,
          bytes: transfer.bytes,
          error: transfer.error
        });
        return;
      }

      if (msg.type === "input-result") {
        const sessionId = String(msg.sessionId || "");
        const controller = controllers.get(sessionId);
          relayLog("input.result", {
            sessionId,
            inputId: String(msg.inputId || ""),
            deviceId: ws.deviceId,
            userId: controller?.userId || "",
            action: String(msg.action || ""),
            ok: Boolean(msg.ok),
            error: String(msg.error || ""),
            permissionDiagnostics: msg.permissionDiagnostics || {}
          });
        if (controller?.ws && controller.ws.readyState === WebSocket.OPEN) {
          send(controller.ws, {
            type: "input-result",
            sessionId,
            inputId: String(msg.inputId || ""),
            deviceId: ws.deviceId,
            action: String(msg.action || ""),
            ok: Boolean(msg.ok),
            error: String(msg.error || "")
          });
        }
        return;
      }
      return;
    }

    if (ws.role === "controller") {
      if (msg.type === "subscribe") {
        send(ws, { type: "devices", devices: listBoundDevices(ws.userId) });
        return;
      }

      if (msg.type === "control") {
        const id = normalizeDeviceId(msg.deviceId);
        const binding = findBinding(ws.userId, id);
        const device = devices.get(id);
        if (!binding) {
          send(ws, { type: "error", error: "device_not_bound" });
          return;
        }
        if (!device) {
          send(ws, { type: "error", error: "device_offline", device: bindingView(binding) });
          return;
        }
        ws.allowRtcRelayFallback = Boolean(msg.relayFallback || msg.allowRelayFallback);
        attachController(device, ws, {
          mode: sanitizeSessionMode(msg.mode),
          reason: msg.reason || ""
        });
        return;
      }

      if (msg.type === "rtc-start") {
        startRtcSession(ws, msg);
        return;
      }

      if (["rtc-offer", "rtc-ice-candidate", "rtc-state", "rtc-stop"].includes(msg.type)) {
        forwardRtcSignal(ws, msg);
        return;
      }

      if (msg.type === "input") {
        const sessionId = String(msg.sessionId || "");
        const controller = controllers.get(sessionId);
        if (!controller || controller.ws !== ws || controller.userId !== ws.userId) {
          send(ws, { type: "error", error: "bad_session", sessionId });
          return;
        }
        const device = devices.get(controller.deviceId);
        if (!device) {
          send(ws, { type: "error", error: "device_offline" });
          return;
        }
        if (!findBinding(ws.userId, device.id)) {
          send(ws, { type: "error", error: "device_not_bound" });
          return;
        }
        if (!canControlDevice(device)) {
          const reason = controlBlockReason(device);
          relayLog("input.blocked", {
            sessionId,
            userId: ws.userId,
            deviceId: device.id,
            action: String(msg.action || ""),
            reason,
            controlEnabled: Boolean(device.controlEnabled),
            permissions: device.permissions || {},
            permissionDiagnostics: device.permissionDiagnostics || {}
          });
          send(ws, { type: "error", error: "input_not_ready", reason, device: deviceView(device) });
          return;
        }
        const inputId = String(msg.inputId || crypto.randomBytes(6).toString("hex"));
        relayLog("input.forward", {
          sessionId,
          inputId,
          userId: ws.userId,
          deviceId: device.id,
          action: String(msg.action || ""),
          x: Number(msg.x || 0),
          y: Number(msg.y || 0),
          x2: Number(msg.x2 || 0),
          y2: Number(msg.y2 || 0)
        });
        send(device.ws, {
          type: "input",
          sessionId,
          inputId,
          action: msg.action,
          x: Number(msg.x || 0),
          y: Number(msg.y || 0),
          x2: Number(msg.x2 || 0),
          y2: Number(msg.y2 || 0),
          duration: Number(msg.duration || 0),
          button: String(msg.button || "left"),
          deltaX: Number(msg.deltaX || 0),
          deltaY: Number(msg.deltaY || 0),
          key: String(msg.key || ""),
          code: String(msg.code || ""),
          text: String(msg.text || ""),
          modifiers: Array.isArray(msg.modifiers) ? msg.modifiers.slice(0, 8).map(String) : []
        });
        return;
      }

      if (msg.type === "frame-ack") {
        const sessionId = String(msg.sessionId || "");
        const controller = controllers.get(sessionId);
        if (!controller || controller.ws !== ws || controller.userId !== ws.userId) return;
        const device = devices.get(controller.deviceId);
        if (!device || device.id !== String(msg.deviceId || "") || device.ws.readyState !== WebSocket.OPEN) return;
        ws.lastFrameAck = {
          deviceId: device.id,
          frameId: msg.frameId || 0,
          timestamp: now()
        };
        send(device.ws, {
          type: "frame-ack",
          sessionId,
          frameId: msg.frameId || 0,
          controllerCount: device.controllers.size
        });
        return;
      }

      if (msg.type === "stop-control") {
        const sessionId = String(msg.sessionId || "");
        const controller = controllers.get(sessionId);
        if (!controller || controller.ws !== ws) return;
        stopControllerSession(sessionId, "controller_stopped", {
          notifyController: true,
          notifyDevice: true,
          broadcast: true
        });
      }
    }
  });

  ws.on("close", () => {
    stopRtcSessionsForWs(ws, "peer_closed");
    if (ws.role === "controller") {
      clients.delete(ws);
      for (const sessionId of Array.from(ws.sessions)) {
        stopControllerSession(sessionId, "controller_closed", {
          notifyController: false,
          notifyDevice: true,
          broadcast: true
        });
      }
    }

    if (ws.role === "device" && ws.deviceId) {
      const device = devices.get(ws.deviceId);
      if (device?.ws === ws) {
        for (const sessionId of Array.from(device.controllers)) {
          stopControllerSession(sessionId, "device_offline", {
            notifyController: true,
            notifyDevice: false,
            broadcast: false
          });
        }
        devices.delete(ws.deviceId);
        broadcastDeviceOwners(ws.deviceId);
      }
    }
  });
});

setInterval(() => {
  for (const ws of wss.clients) {
    if (ws.isAlive === false) {
      ws.missedPongs = Number(ws.missedPongs || 0) + 1;
      if (ws.missedPongs >= WS_MAX_MISSED_PONGS) {
        ws.terminate();
        continue;
      }
    }
    ws.isAlive = false;
    if (ws.readyState === WebSocket.OPEN) {
      ws.ping();
    }
  }
  const removed = pruneDevices();
  for (const deviceId of removed) {
    broadcastDeviceOwners(deviceId);
  }
  pruneFileTransfers();
  pruneRtcSessions();
  pruneRelaySessions();
}, WS_PING_INTERVAL_MS).unref();

server.listen(PORT, HOST, () => {
  console.log(`BHZN ToDesk server listening on http://${HOST}:${PORT}`);
});
