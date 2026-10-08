# 设备接入协议：设备 ↔ AI 调度助手（v1）

> 适用：ESP32 类语音终端（示例 `ESP32_A1B2C3`）。
> 目标：设备用绑定得到的 **device_token** 直接调用我们的 AI 调度助手（mastra），
> 既能**提交文本 / 上传音频（服务端转写）**，也能**轮询领取 AI 下发的命令**。

---

## 一、整体链路

```
┌────────┐  ①扫码局域网友好配对          ┌──────────┐
│ 设备    │ ─── 二维码 http://ip:8080/pair?code=XXXX ──▶ │  手机 App │
│        │                                            │          │
│        │  ②POST http://ip:8080/api/device/bind       │          │
│        │ ◀── {bind_ticket, user_token, expires_at, api_base_url} │
└────────┘                                            └──────────┘
     │ ③设备拿 bind_ticket 访问云端                      │
     ▼                                                  │
┌───────────────────────────────────────────────────────────────┐
│ 我们的服务器（https://bhzn.top）                                │
│  POST /api/device/report         → 建立绑定 + 下发 device_token  │
│  POST /api/device/message        → 文本送 AI 调度助手            │
│  GET  /api/device/message/{id}   → 取回复（可长轮询）             │
│  POST /api/device/audio          → 音频转写后送 AI 调度助手       │
│  GET  /api/device/commands       → 轮询领取 AI 下发的命令        │
│  POST /api/device/commands/{id}/ack → 回报执行结果               │
│  POST /api/device/heartbeat      → 心跳                        │
└───────────────────────────────────────────────────────────────┘
     │ ④设备文本/语音等价于「该用户在 AI 调度助手里说了一句话」
     ▼
  AI 调度助手（mastra）：现有管线 /api/mastra-chat/messages
```

**关键语义**：设备提交的内容，会以「绑定该设备的那个账号」的身份进入 AI 调度助手。
回复也写回同一个会话，App / H5 / 设备三端看到的是同一份记录。

---

## 二、配对与拿 token

### 1) 设备侧（固件已具备）
- 启动后连上 WiFi，屏幕显示二维码：`http://<本机IP>:8080/pair?code=<一次性配对码>`
- 提供 `POST /api/device/bind`（供手机 App 调用），接受：
```json
{
  "bind_ticket": "bt_xxx",      // 我们服务器签发的一次性票据（推荐）
  "user_token": "<App当前Token>", // 兼容字段（老固件用；建议逐步废弃）
  "expires_at": 1791504000,
  "api_base_url": "https://bhzn.top",
  "code": "8F4C92D7"
}
```

### 2) 手机 App（已实现，H5 页 `https://h5.bhzn.top/h5-static/device-bind.html`）
1. 扫码 → 解析出设备 IP + 配对码
2. `POST /api/device/bind-ticket`（App 登录态）→ `{ticket, expires_at, expires_in:300, api_base_url}`
3. 通过原生局域网桥把上面的 JSON POST 给设备

### 3) 设备回报云端（设备唯一必须调通的"入网"接口）
```http
POST https://bhzn.top/api/device/report
Content-Type: application/json

{"ticket":"bt_xxx","device_id":"ESP32_A1B2C3","name":"智能语音终端","protocol_version":1}
```
返回（**device_token 只在这里返回一次，请存到 NVS/Flash**）：
```json
{"ok":true,"device_token":"dev_xxxxxxxx","device_id":"ESP32_A1B2C3","name":"智能语音终端",
 "user_id":31,"bind_status":"bound","protocol_version":1,"api_base_url":"https://bhzn.top"}
```
其它查询：`GET /api/device/bind/status?device_id=ESP32_A1B2C3` →
`{"device_id":"ESP32_A1B2C3","name":"智能语音终端","bind_status":"waiting|bound|expired","protocol_version":1}`

---

## 三、鉴权

| 项 | 值 |
|---|---|
| 头 | `Authorization: Bearer dev_xxxxxxxx`（也支持 `X-Device-Token`） |
| 存储 | 服务端只存 `sha256(token)`，明文只在绑定响应里出现一次 |
| 绑定关系 | token ⇒ 设备 ⇒ 归属用户（`bound_devices.user_id`）；所有调用都按该用户执行 |
| 传输 | 必须 HTTPS（生产域名 `https://bhzn.top`）；HTTP 仅限局域网配网那一步 |
| 失效 | 设备解绑/换绑后 token 立即失效（`device_token_hash` 被覆盖） |

---

## 四、设备 → 云端：提交文本

```http
POST /api/device/message
Authorization: Bearer dev_xxxxxxxx
Content-Type: application/json

{"text":"帮我把今天的会议纪要整理成待办","session_id":""}
```
返回：
```json
{"ok":true,"message_id":"9f2c...","session_id":"...","status":"pending","internal_token":"<短期用户token>","events":[]}
```
- `session_id` 传空 = 复用该用户最近一个会话；传具体值表示指定会话（会话必须属于该用户）
- `internal_token`：10 分钟有效的用户 token（设备可用它直连 AI 调度助手的高级接口；不需要就忽略）

### 取回复（推荐长轮询，省流量）
```http
GET /api/device/message/9f2c...?wait=20
Authorization: Bearer dev_xxxxxxxx
```
```json
{"ok":true,"message_id":"9f2c...","status":"completed","reply_text":"已整理 3 条待办：…","reply_audio_url":""}
```
- `wait=0..60`：服务端最多阻塞这么多秒，直到有回复或超时（超时返回 `timeout:true`）
- `status`：`pending`（排队）/ `running`（执行中）/ `completed` / `failed`

---

## 五、设备 → 云端：上传音频（服务端转写）

```http
POST /api/device/audio
Authorization: Bearer dev_xxxxxxxx
Content-Type: multipart/form-data

file=<wav/mp3/m4a/opus…，≤20MB>
session_id=            # 可空
transcribe_only=0      # 1 = 只转写不送 AI
```
返回：
```json
{"ok":true,"text":"帮我把今天的会议纪要整理成待办","audio_url":"https://bhzn.top/h5-static/device-audio/dev_31_xxx.wav",
 "message_id":"9f2c...","status":"pending"}
```
- 音频被存到 `h5_static/device-audio/`（公网可读），随后调用速推 STT 转写
- 建议采样率 16k、单声道、PCM/WAV；单条 ≤ 30 秒体验最好
- `transcribe_only=1` 时只返回 `text`（可用于设备端本地确认/纠错）
- 转写失败：`502 {"detail":"转写失败或结果为空"}`

---

## 六、AI → 设备：命令下发（设备轮询）

设备**主动轮询**领命令（嵌入式最省电、无需公网反向连接）：
```http
GET /api/device/commands?wait=25
Authorization: Bearer dev_xxxxxxxx
```
```json
{"ok":true,"commands":[
  {"id":12,"kind":"text","payload":{"text":"提醒我 10 分钟后开会","tts":true}},
  {"id":13,"kind":"play","payload":{"audio_url":"https://…/a.mp3"}}
]}
```
- `wait=0..55` 长轮询；没有命令时到点返回 `{"ok":true,"commands":[],"timeout":true}`
- 命令一旦下发即标记 `sent`（不会重复领取）

回报执行结果：
```http
POST /api/device/commands/12/ack
Authorization: Bearer dev_xxxxxxxx
{"status":"done","result":{"played":true}}
```

**命令从哪来**：AI 调度助手（mastra）侧配置一个 MCP 工具（`device.send`）调用服务端
`enqueue_device_command(device_id, kind, payload)`；用户对 AI 说「让设备播报一下」，
AI 就会往队列里放一条命令，设备下次轮询即可拿到。（工具注册见文末「待接」）

---

## 七、心跳

```http
POST /api/device/heartbeat
Authorization: Bearer dev_xxxxxxxx
{"battery":86}
```
```json
{"ok":true,"server_time":1791504000,"device_id":"ESP32_A1B2C3"}
```
建议 30–60 秒一次；服务端据此更新 `last_seen_at`（管理后台可看在线状态）。

---

## 八、状态码约定

| HTTP | 含义 | 设备侧建议 |
|---|---|---|
| 200 | 成功 | — |
| 400 | 参数不合法（缺 text / status 非 done/failed） | 修正后重试，别重试不停 |
| 401 | device_token 缺失或无效 | 提示"设备未绑定"，回到配网流程 |
| 404 | message_id / command_id 不存在，或不属于该设备用户 | 丢弃本地缓存 |
| 410 | 绑定票据过期 | 重新扫码配网 |
| 413 | 音频超过 20MB | 分段上传 |
| 429 | 触发限流 | 退避重试（建议 2/5/10 秒） |
| 502 | 上游转写失败 | 退避重试 1 次，仍失败提示用户 |
| 503/504 | 服务端维护/超时 | 指数退避 |

---

## 九、ESP32 参考伪代码

```cpp
// 1) 绑定（只需一次，device_token 存 NVS）
http.POST("https://bhzn.top/api/device/report",
          {"ticket": cfg.bind_ticket, "device_id": cfg.device_id,
           "name": "智能语音终端", "protocol_version": 1});
// 解析返回的 device_token → nvs.set("dev_token", ...)

// 2) 主循环
loop() {
  if (millis() - lastPoll > 1500) {            // 命令轮询（长轮询 25s）
    auto r = http.GET("/api/device/commands?wait=25", bearer(dev_token));
    for (auto &c : r.commands) {
      if (c.kind == "play") player.play(c.payload.audio_url);
      if (c.kind == "text") speaker.tts(c.payload.text);
      http.POST("/api/device/commands/" + c.id + "/ack", {"status":"done"});
    }
    lastPoll = millis();
  }
  if (buttonPressed()) {                        // 用户说话
    auto wav = recorder.recordSeconds(10);      // 或本机 STT
    auto up = http.POSTmultipart("/api/device/audio", wav, bearer(dev_token));
    auto msgId = up.message_id;
    auto rep = http.GET("/api/device/message/" + msgId + "?wait=20", bearer(dev_token));
    speaker.playText(rep.reply_text);           // 需要语音回包时见下
  }
  if (millis() - lastBeat > 45000) { http.POST("/api/device/heartbeat", {"battery": bat()}); lastBeat = millis(); }
}
```

---

## 十、与「AI 调度助手 → mastra」的对应关系

| 设备动作 | 服务端内部 | mastra 侧 |
|---|---|---|
| `POST /api/device/message` | 直接调用 `mastra_chat.create_mastra_message`（同一管线、同一张 `h5_chat_messages` 表） | 与 App/H5 发消息完全一致，能力/记忆/知识库照旧生效 |
| `GET /api/device/message/{id}` | 读 `h5_chat_messages.reply_text / status` | 回复由现有执行链路写回 |
| `POST /api/device/audio` | 上传→公网 URL→速推 STT→文本→同上管线 | 无感知 |
| `GET /api/device/commands` | `device_commands` 队列表 | 需注册 MCP 工具 `device.send`（待接） |

---

## 十一、待接 / 待确认

1. **mastra 工具 `device.send`**：让 AI 主动下发命令（服务端函数 `device_ai.enqueue_device_command` 已就绪，只差注册工具）。
2. **语音回包（TTS）**：目前只返回文本；要设备播报可加 `tts=1` 参数，服务端调现有语音合成后返回 `reply_audio_url`。
3. **设备侧字段确认**：固件 `POST /api/device/bind` 实际接受的字段名；如需去掉 `bind_ticket` 只用 `user_token`，服务端可兼容。
4. **限流策略**：建议按 device_token 限速（如 60 次/分钟、音频 10 条/小时），需要的话我加。
