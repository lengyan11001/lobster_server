# 设备接入 AI 调度 · 对接文档

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 更新日期 | 2026-10-08 |
| 适用对象 | 智能语音终端等嵌入式设备厂商（示例机型 `ESP32_A1B2C3`） |
| 服务地址 | `https://bhzn.top`（生产，必须使用 HTTPS） |
| 协议 | HTTP/1.1 + JSON（音频走 `multipart/form-data`） |

> 设备接入后即可通过云端 AI 调度助手完成：**语音/文本提问 → AI 回答**、**接收 AI 下发的执行命令**。
> 设备的每一次交互都以「绑定它的那个账号」的身份进入 AI 调度，回复与 App/H5 端共用同一份会话记录。

---

## 1. 角色与流程

| 角色 | 职责 |
|---|---|
| 设备 | 显示二维码；实现本地 `/pair`、`/api/device/bind`；绑定后轮询云端命令、上报执行结果 |
| 手机 App（我方） | 扫二维码 → 取一次性票据 → 把票据投递给设备 |
| 云端（我方） | 签发票据、建立绑定、下发设备 token；转发设备文本/语音到 AI 调度；排队下发命令 |

### 1.1 整体时序

```
  设备                        手机 App                        云端
   │  ①显示二维码                │                              │
   │  http://ip:8080/pair?code=X │                              │
   │ ──────────────────────────▶ │ ②扫码解析                    │
   │                             │ ③取票据 POST /api/device/bind-ticket
   │                             │ ───────────────────────────▶ │
   │                             │ ◀── {ticket, expires_at, api_base_url}
   │ ④POST http://ip:8080/api/device/bind                        │
   │ ◀────────────────────────── │   {bind_ticket, user_token, expires_at, api_base_url}
   │                             │                              │
   │ ⑤设备回报云端 POST /api/device/report                       │
   │ ───────────────────────────────────────────────────────────▶│
   │ ◀───────────────────────────────── {"device_token":"dev_…"} │
   │                             │                              │
   │ ⑥文本 POST /api/device/message  ──────────────────────────▶ │ 送 AI 调度
   │ ⑦语音 POST /api/device/audio    ──────────────────────────▶ │ 转写 → 送 AI 调度
   │ ⑧取回复 GET  /api/device/message/{id}?wait=20 ────────────▶ │
   │ ⑨领命令 GET  /api/device/commands?wait=25 ────────────────▶ │ AI 下发的命令
   │ ⑩心跳   POST /api/device/heartbeat ──────────────────────▶ │
```

---

## 2. 鉴权

| 项 | 说明 |
|---|---|
| 请求头 | `Authorization: Bearer <device_token>`（也支持 `X-Device-Token: <device_token>`） |
| device_token | 在 **绑定回报**（第 5 步）时下发，**只返回一次**，设备需写入 NVS/Flash |
| 服务端留存 | 仅存 `sha256(token)`，无法反推 |
| 归属 | token → 设备 → 绑定它的用户账号；所有调用均以该账号身份执行 |
| 失效 | 设备被解绑/换绑后立即失效（返回 401，需要重新配对） |
| 传输 | 必须 HTTPS；仅局域网配网那一步（第 ①–④ 步）用设备本地 HTTP |

---

## 3. 设备侧需实现的接口（本机 HTTP:8080）

### 3.1 展示二维码
画面显示：`http://<设备当前局域网IP>:8080/pair?code=<一次性配对码>`

- `code`：8 位随机码（示例 `8F4C92D7`），建议 5 分钟有效、绑定成功即作废、连续失败 5 次后更换
- 每次重启/换网后二维码里的 IP 需更新

### 3.2 接收 App 投递

```http
POST http://<设备IP>:8080/api/device/bind
Content-Type: application/json

{
  "bind_ticket": "bt_xxxxxxxx",
  "user_token": "<App 当前登录 token>",
  "expires_at": 1791504000,
  "api_base_url": "https://bhzn.top",
  "code": "8F4C92D7"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `bind_ticket` | 是 | 云端签发的一次性票据（5 分钟有效，只能用一次），设备拿它去云端换 `device_token` |
| `user_token` | 否 | 兼容字段；设备**不要长期保存**，绑定完成后即可丢弃 |
| `expires_at` | 否 | 票据过期时间（epoch 秒），可用于本地校验 |
| `api_base_url` | 否 | 云端地址，默认 `https://bhzn.top` |
| `code` | 否 | 配对码，设备可校验是否与屏幕上的一致 |

响应：`{"ok":true}` 或 `{"ok":false,"reason":"code_mismatch"}`

> 设备本地 HTTP 只需支持这一个 POST；其余交互全部由设备主动调用云端。

---

## 4. 云端接口总览

| # | 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|---|
| 1 | POST | `/api/device/report` | 票据 | 建立绑定、下发 `device_token` |
| 2 | GET | `/api/device/bind/status` | 无 | 查询绑定状态（`ticket` 或 `device_id`） |
| 3 | POST | `/api/device/message` | 设备 token | 提交文本给 AI 调度 |
| 4 | GET | `/api/device/message/{message_id}` | 设备 token | 取 AI 回复（支持长轮询） |
| 5 | POST | `/api/device/audio` | 设备 token | 上传音频 → 服务端转写 → 送 AI |
| 6 | GET | `/api/device/commands` | 设备 token | 轮询领取 AI/后台下发的命令 |
| 7 | POST | `/api/device/commands/{id}/ack` | 设备 token | 回报命令执行结果 |
| 8 | POST | `/api/device/heartbeat` | 设备 token | 心跳与在线状态 |
| 9 | POST | `/api/device/bind-ticket` | App 登录态 | （App 用）换一次性票据 |
| 10 | GET | `/api/device/list` | App 登录态 | （App 用）查看已绑定设备 |

---

## 5. 接口详情

### 5.1 建立绑定

```http
POST /api/device/report
Content-Type: application/json

{"ticket":"bt_xxxxxxxx","device_id":"ESP32_A1B2C3","name":"智能语音终端","protocol_version":1}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `ticket` | 是 | 第 3.2 步收到的 `bind_ticket` |
| `device_id` | 是 | 设备唯一号（建议 `芯片型号_序列号`，≤128 字符，全局唯一） |
| `name` | 否 | 展示名，如「智能语音终端」 |
| `protocol_version` | 否 | 协议版本，当前 `1` |

成功响应：

```json
{
  "ok": true,
  "device_token": "dev_xxxxxxxxxxxxxxxx",
  "device_id": "ESP32_A1B2C3",
  "name": "智能语音终端",
  "bind_status": "bound",
  "protocol_version": 1,
  "api_base_url": "https://bhzn.top"
}
```

失败：`404`（票据不存在）、`410`（票据过期，需用户重新扫码）、`400`（缺字段）。

### 5.2 查询绑定状态

```http
GET /api/device/bind/status?device_id=ESP32_A1B2C3
GET /api/device/bind/status?ticket=bt_xxxxxxxx
```

```json
{"device_id":"ESP32_A1B2C3","name":"智能语音终端","bind_status":"bound","protocol_version":1}
```

`bind_status`：`waiting`（票据已签发但设备尚未回报）/ `bound`（已绑定）/ `expired`（票据过期）。

### 5.3 提交文本

```http
POST /api/device/message
Authorization: Bearer dev_xxxxxxxx
Content-Type: application/json

{"text":"帮我把今天的会议纪要整理成待办","session_id":""}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `text` | 是 | 用户问题/指令，≤8000 字 |
| `session_id` | 否 | 空 = 该账号最近会话；指定时必须属于同一账号 |

```json
{"ok":true,"message_id":"9f2c1a…","session_id":"…","status":"pending"}
```

### 5.4 取回复

```http
GET /api/device/message/9f2c1a…?wait=20
Authorization: Bearer dev_xxxxxxxx
```

```json
{"ok":true,"message_id":"9f2c1a…","status":"completed","reply_text":"已整理 3 条待办：…","reply_audio_url":""}
```

| 字段 | 说明 |
|---|---|
| `status` | `pending` 排队 / `running` 执行中 / `completed` 完成 / `failed` 失败 |
| `reply_text` | AI 回复文本（TTS 场景可直接播报） |
| `reply_audio_url` | 语音回复地址，当前为空；开通 TTS 后返回 |
| `wait` | 长轮询秒数 0–60；到点无回复时返回 `timeout:true` |

### 5.5 上传音频（服务端转写）

```http
POST /api/device/audio
Authorization: Bearer dev_xxxxxxxx
Content-Type: multipart/form-data

file=<二进制音频>
session_id=
transcribe_only=0
```

| 参数 | 说明 |
|---|---|
| `file` | 音频文件，支持 `wav / mp3 / m4a / aac / ogg / opus / amr / pcm / silk`，≤20MB |
| `transcribe_only` | `1` = 只转写，不送 AI（便于设备端做本地确认） |
| `session_id` | 同 5.3 |

```json
{"ok":true,"text":"帮我把今天的会议纪要整理成待办",
 "audio_url":"https://bhzn.top/h5-static/device-audio/dev_31_xxx.wav",
 "message_id":"9f2c1a…","status":"pending"}
```

建议：16 kHz、单声道、PCM/WAV；单条 ≤30 秒体验最佳。
转写失败：`502 {"detail":"转写失败或结果为空"}`。

### 5.6 领取命令

```http
GET /api/device/commands?wait=25
Authorization: Bearer dev_xxxxxxxx
```

```json
{"ok":true,"commands":[
  {"id":12,"kind":"text","payload":{"text":"提醒我 10 分钟后开会"}},
  {"id":13,"kind":"play","payload":{"audio_url":"https://…/a.mp3"}}
]}
```

- 命令类型由业务约定，当前示例：`text`（播报文本）、`play`（播放音频）、`record`（开始录音）
- 领取即标记为已下发，不会重复领取；无命令时长轮询到点返回 `{"commands":[],"timeout":true}`

### 5.7 回报执行结果

```http
POST /api/device/commands/12/ack
Authorization: Bearer dev_xxxxxxxx

{"status":"done","result":{"played":true}}
```

`status`：`done` / `failed`（其它值返回 400）。

### 5.8 心跳

```http
POST /api/device/heartbeat
Authorization: Bearer dev_xxxxxxxx

{"battery":86}
```

```json
{"ok":true,"server_time":1791504000,"device_id":"ESP32_A1B2C3"}
```

---

## 6. 轮询节奏建议（省电 + 省流量）

| 动作 | 间隔 | 说明 |
|---|---|---|
| 命令轮询 | 长轮询 `wait=25`，循环执行 | 空闲时可放宽到 60 秒 |
| 回复查询 | 提交后长轮询 `wait=20` | 拿到 `completed` 即停 |
| 心跳 | 30–60 秒 | 服务端据此判断在线 |
| 断线重连 | 2s → 5s → 10s → 30s 退避 | 连续失败 5 次提示"网络异常" |

---

## 7. 错误码

| HTTP | 含义 | 设备处理 |
|---|---|---|
| 200 | 成功 | — |
| 400 | 参数不合法 | 修正参数后重试（不要无限重试） |
| 401 | device_token 缺失/无效 | 提示"设备未绑定"，回到配网流程 |
| 404 | message/command 不存在或不属于本设备归属账号 | 丢弃本地缓存 |
| 410 | 绑定票据过期 | 让用户重新扫码 |
| 413 | 音频超过 20MB | 分段上传 |
| 429 | 触发限流 | 退避重试（2/5/10 秒） |
| 502 | 上游转写失败 | 退避重试 1 次，仍失败提示用户 |
| 503 / 504 | 服务维护 / 网关超时 | 指数退避 |

---

## 8. 嵌入式参考实现（伪代码）

```cpp
// 1) 绑定：POST /api/device/report  →  device_token 写入 NVS（只出现一次）
void onBindTicket(const char* ticket) {
  auto r = httpPost("https://bhzn.top/api/device/report",
                    json{{"ticket", ticket}, {"device_id", DEVICE_ID},
                         {"name", "智能语音终端"}, {"protocol_version", 1}});
  if (r.ok) nvs.set("dev_token", r.device_token);
}

// 2) 主循环
void loop() {
  if (millis() - lastPoll > 1500) {
    auto r = httpGet("/api/device/commands?wait=25", bearer(nvs.get("dev_token")));
    for (auto& c : r.commands) {
      if (c.kind == "play")  player.play(c.payload.audio_url);
      if (c.kind == "text")  speaker.tts(c.payload.text);
      httpPost("/api/device/commands/" + String(c.id) + "/ack",
               json{{"status", "done"}}, bearer(nvs.get("dev_token")));
    }
    lastPoll = millis();
  }
  if (buttonPressed()) {                        // 用户按下说话
    auto wav = recorder.recordSeconds(10);
    auto up  = httpPostAudio("/api/device/audio", wav, bearer(nvs.get("dev_token")));
    auto rep = httpGet("/api/device/message/" + up.message_id + "?wait=20",
                       bearer(nvs.get("dev_token")));
    speaker.playText(rep.reply_text);
  }
  if (millis() - lastBeat > 45000) {
    httpPost("/api/device/heartbeat", json{{"battery", batteryLevel()}},
             bearer(nvs.get("dev_token")));
    lastBeat = millis();
  }
}
```

---

## 9. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| App 扫完码调设备接口失败 | 手机与设备不在同一局域网（用了 4G/5G、或路由器开了 AP 隔离） | 手机连设备所在 WiFi；路由器关闭"客户端隔离" |
| 设备回报 `410` | 票据 5 分钟过期 | 让用户重新扫码 |
| 设备调用返回 `401` | token 失效（被解绑/换绑） | 重新配对 |
| 转写 `502` | 音频格式不支持或上游波动 | 改用 16k 单声道 WAV，退避重试一次 |
| 命令一直领不到 | AI/后台未下发 | 确认该设备已绑定，且业务侧已投递命令 |

---

## 10. 版本与变更

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-08 | 首版：配对绑定、文本、音频转写、回复、命令下发、心跳 |
