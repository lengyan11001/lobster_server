# 设备语音接入（实时流式识别）· 对接文档

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 更新日期 | 2026-10-10 |
| 适用对象 | 智能语音终端等嵌入式设备厂商（示例机型 `ESP32_A1B2C3`） |
| 服务地址 | `https://bhzn.top`（必须 HTTPS / WSS） |
| 协议 | WebSocket（`wss://`） + JSON 控制帧 + 16bit PCM 二进制音频帧 |

本文说两件事：
1. **首选**：设备边说话边把音频流推给云端，云端**实时转成文字**（边说边出中间结果），说完立刻拿最终文本送 AI 调度 —— 不用等整段录完再上传；
2. 与另一条路（整段录音上传文件 `POST /api/device/audio`）的区别见文末。

---

## 1. 与"上传音频文件"方式的对比

| | **实时流式（本文）** | 上传音频文件 |
|---|---|---|
| 接口 | `wss://bhzn.top/api/h5-chat/voice/session` | `POST /api/device/audio` |
| 体验 | 边说边出字（300~600ms 出中间结果），说完即得文本 | 录完再传，等待整体转写（约 1~3 秒） |
| 带宽 | 全程持续占用（16k/16bit ≈ 32 KB/s） | 一次性上传（同一段音频同样大小） |
| 音频格式 | **16kHz / 16bit / 单声道 PCM**（原始字节流） | wav/mp3/m4a… 文件，≤20MB |
| 适用 | 语音问答、唤醒后对话 | 定时上传、弱网重传、已有录音文件 |

两条路最终都会把**文本**交给同一套 AI 调度（同一账号、同一会话记录）。

---

## 2. 鉴权

WebSocket 不能带 `Authorization` 头，所以用 query 传：
```
wss://bhzn.top/api/h5-chat/voice/session?device_token=dev_xxxxxxxx&installation_id=<可选>
```
- `device_token`：**设备绑定后拿到的长期 token**（`POST /api/device/report` 返回，只返回一次，存 NVS/Flash）；
- 服务端按 `sha256(device_token)` 查绑定表，找到**绑定它的账号**，之后一切交互都以该账号身份执行；
- 鉴权失败：服务端发 `{"type":"error","message":"登录已失效，请重新登录后再试"}` 然后 **关闭连接，close code = 4401**，设备应重新走配对绑定；
- 也支持 `?token=<用户 JWT>`（H5/App 用），设备不需要。

---

## 3. 交互时序

```
设备                                         云端
 │ ① 连接 wss://…/voice/session?device_token=dev_x
 │ ───────────────────────────────────────────▶│  校验 token → 找到归属账号
 │                                             │
 │ ② {"type":"start"}                          │  云端连接讯飞实时识别
 │ ───────────────────────────────────────────▶│
 │ ◀── {"type":"listening","provider":"xfyun"} │
 │                                             │
 │ ③ 二进制帧：16k/16bit/单声道 PCM（建议每帧 20~40ms）
 │ ───────────────────────────────────────────▶│  转发上游
 │ ◀── {"type":"partial","text":"我讲一句"}     │  边说边出中间结果（可选展示）
 │ ◀── {"type":"partial","text":"我讲一句话"}   │
 │                                             │
 │ ④ {"type":"stop"}                           │  通知上游"说完了"
 │ ───────────────────────────────────────────▶│
 │ ◀── {"type":"final","text":"我讲一句话"}     │  ← 最终文本
 │ ◀── {"type":"intent","…":…}                 │  ← 意图解析（可关，见 resolve_intent）
 │                                             │
 │ ⑤ 用最终文本调 POST /api/device/message {"text":"我讲一句话"}
 │ ───────────────────────────────────────────▶│  进 AI 调度（= 和 App/H5 同一条链路）
 │ ◀── {"ok":true,"message_id":"…","status":"pending"}
 │ ⑥ GET /api/device/message/{id}?wait=20      │  长轮询取 AI 回复
```

心跳：设备可随时发 `{"type":"ping"}`，云端回 `{"type":"pong"}`（建议 20~30 秒一次；上游侧也有 20 秒级 ping）。

---

## 4. 控制帧（设备 → 云端）

| JSON | 说明 |
|---|---|
| `{"type":"start"}` | 开始一段说话。云端连上游，成功后回 `{"type":"listening"}`；失败回 `{"type":"error","message":"…"}` |
| `{"type":"stop"}` | 说完了，云端通知上游出最终结果 |
| `{"type":"ping"}` | 心跳，回 `{"type":"pong"}` |

## 5. 事件（云端 → 设备）

| JSON | 说明 |
|---|---|
| `{"type":"listening","provider":"xfyun"}` | 已就绪，可以推音频 |
| `{"type":"partial","text":"…"}` | 中间结果（可能多次、可能回退，仅用于屏幕预览） |
| `{"type":"final","text":"…"}` | **最终文本**，用它去调 `/api/device/message` |
| `{"type":"intent","…"}` | 意图解析结果（不需要就传 `resolve_intent=false` 关掉） |
| `{"type":"pong"}` | 心跳回应 |
| `{"type":"error","message":"…"}` | 出错；带 `code=provider_not_configured` 表示云端实时识别未配置 |

## 6. 音频格式要求（重要）

- **采样率 16000 Hz、位深 16bit、单声道、小端 PCM 原始数据**（不要 wav 头）；
- 每帧建议 **20~40ms（640~1280 字节）**，连续推送；不要一次性把整段塞进去；
- 说之前先发 `start`，说完发 `stop`（不发 `stop` 拿不到 `final`）；
- 建议单段 ≥2 秒（太短识别结果会很差）。

## 7. 设备侧参考实现（伪代码）

```cpp
// 1) 连接（用绑定时拿到的 device_token）
auto ws = websocketConnect("wss://bhzn.top/api/h5-chat/voice/session?device_token=" + devToken);

// 2) 开始说话
ws.sendText("{\"type\":\"start\"}");
// 等 {"type":"listening"}，同时异步收 {"type":"partial"} 显示在屏幕上

// 3) 录音并以 16k/16bit/单声道 PCM 持续发送
mic.begin(16000, 1, 16);
while (buttonHeld()) {
  auto pcm = mic.read(640);           // 20ms
  ws.sendBinary(pcm.data(), pcm.size());
}
// 4) 说完了
ws.sendText("{\"type\":\"stop\"}");
// 等 {"type":"final","text":"…"}  → 得到文本
// 5) 把文本交给 AI 调度（HTTP，走已有的设备鉴权头）
auto r = httpPostJson("https://bhzn.top/api/device/message",
                      "{\"text\":\"" + text + "\"}", bearer(devToken));
// 6) 取回复
auto rep = httpGet("https://bhzn.top/api/device/message/" + r.message_id + "?wait=20", bearer(devToken));
```

## 8. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 连上就被关，close 4401 | `device_token` 无效或设备已解绑 → 重新配对 |
| `provider_not_configured` | 云端实时识别未配置（联系我们） |
| 一直只有 partial，没有 final | 忘了发 `{"type":"stop"}` |
| final 文本为空 | 音频太短/太吵；确认是 16k 单声道 16bit PCM（不是 wav 文件流） |
| 想省事、不想做流式 | 用 `POST /api/device/audio` 整段上传（见《设备接入 AI 调度 · 对接文档》5.5 节） |

## 9. 版本与变更

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-09 | 首版：实时流式语音识别（start/stop/ping + PCM 推流 + partial/final/intent） |

---

## 10. 语音回包 TTS（可选，设备开关控制）

默认**只回文本**；设备上做一个开关，打开时在请求里带 `tts=1`，云端才会把 AI 回复合成语音。

### 10.1 怎么开

| 位置 | 示例 | 说明 |
|---|---|---|
| 提交文本 | `POST /api/device/message` body `{"text":"今天天气怎么样","tts":1}` | 也可用 query：`/api/device/message?tts=1` |
| 取回复（**权威**） | `GET /api/device/message/{id}?wait=20&tts=1` | 只要取回复时带 `tts=1` 就一定会合成 |

取值：`1/true/yes/on` = 开；`0/false/空/不传` = 关。

### 10.2 返回字段

```json
{"ok":true,"status":"completed","reply_text":"今天晴，26 度…",
 "reply_audio_url":"https://bhzn.top/api/device/audio/tts_<消息id>.mp3",
 "tts_credits":1,                 // 本次扣了多少积分（缓存命中时为 0）
 "tts_cached":false,              // 是否复用了已合成的音频
 "tts_error":""}                  // 合成失败时的原因（此时上面的 url 为空）
```
- 音频：**mp3 / 16k 单声道**，直接 GET 播放（公开地址，无需鉴权）；
- **同一条消息只合成一次**：重复查询复用文件，`tts_cached=true`、`tts_credits=0`；
- **合成失败不影响文本**：`reply_text` 照常返回，只是 `reply_audio_url` 为空并带 `tts_error`；
- **计费**：从**绑定该设备的账号**扣积分，`20 积分/1000 字符`（Turbo），**起步 1 积分**，单条最多合成前 **300 字**；余额不足 → 只回文本 + `tts_error:"算力不足…"`。

### 10.3 设备侧伪代码

```cpp
bool ttsOn = settings.ttsEnabled();               // 设备上的开关
auto url = String("/api/device/message/") + msgId + "?wait=20" + (ttsOn ? "&tts=1" : "");
auto rep = httpGet(url, bearer(devToken));
if (rep.reply_audio_url.length()) player.play(rep.reply_audio_url);   // 有音频就播
else if (rep.reply_text.length()) speaker.tts(rep.reply_text);        // 没音频就本地播报
```


---

## 11. 流式回复 + 逐句语音（设备 ask，2026-10-10 新增）

原来设备只能「先提交文本，再轮询整段回复」。现在同一条语音 WS 上可以直接发一条 `ask`，
云端在**模型还在生成**时就把回复增量推回来，并按句返回语音地址 —— 设备可以边收边显示、边收边播。

### 11.1 怎么用

```json
{"type":"ask","text":"帮我看看今天有什么安排","tts":1,"session_id":"可选，缺省沿用设备默认会话"}
```

- `text`：要问的内容（必填，空字符串会回 `error`）；
- `tts`：`1/true/yes/on/y` 才逐句合成语音（费用从绑定该设备的账号扣）；
- 同一条连接上再发 `ask` 会**取消上一条未完成的流**，以最新一条为准；
- 连接断开时未完成的流自动取消。

### 11.2 事件（云端 → 设备，按顺序）

| 事件 | 何时到 | 关键字段 |
|---|---|---|
| `reply_accepted` | 已送进 AI 调度（拿到 message_id） | `message_id` / `session_id` / `status` / `tts` |
| `reply_delta` | 正文有新增（通常 0.3~0.5s 内首字） | `seq`（从 1 递增）/ `delta`（本次新增）/ `text`（当前全文）/ `chars` |
| `reply_audio` | 某一句**已经封口**（≥20 字）且合成成功 | `index`（第几句）/ `text` / `chars` / `credits` / `cached` / `url`（`/api/device/audio/xxx.mp3`）/ `abs_url`（有公网基址时） |
| `reply_audio_error` | 该句合成失败（文本照常返回） | `index` / `text` / `error` |
| `reply_audio_full` | 收尾：整段合并音频 | `url` / `abs_url` / `segments` / `chars` |
| `reply_done` | 本条流结束（成功或失败） | `status` / `text` / `segments` / `credits` / `audio_full_url` / `timeout` |
| `reply_error` | 提交失败 / 消息不存在 / 异常 / 任务 failed | `message`（`message_id` 可能有） |

约定：

- **首字 < 1.5s**：delta 来自服务端流式落库（节流 0.3s），不是等整段结束；
- **逐句语音 ≥20 字才合成**：更短的句子不单独合成，会并入收尾的 `reply_audio_full`；
- **同句只合成一次**：按 `消息ID_句序号` 缓存到 `h5_static/device-audio/tts_*.mp3`，重发不会重复扣费；
- `reply_audio_full` 是**按序拼接**各句音频（不额外扣费）；整条回复都不足 20 字时改为整段合成一次；
- 音频地址走 `/api/device/audio/<名>`（无需 token，设备直接 GET 播放即可）。

### 11.3 设备侧伪代码

```c
ws_send("{\"type\":\"ask\",\"text\":\"今天天气怎么样\",\"tts\":1}");
while (1) {
    msg = ws_recv_json();
    switch (msg.type) {
    case "reply_delta":       ui_append(msg.delta); break;          // 边收边显示
    case "reply_audio":       play_url(msg.abs_url ? msg.abs_url : base_url + msg.url); break;
    case "reply_audio_error": log_warn(msg.error); break;           // 只影响语音，不影响文字
    case "reply_audio_full":  save_last_audio(msg.url); break;      // 需要重播整段时用
    case "reply_done":        ui_finish(msg.status); goto done;
    case "reply_error":       ui_error(msg.message); goto done;
    default:                  break;
    }
}
done:
```

> 说明：`seq` 只在本次 `ask` 内递增；`index` 是句序号。设备只要按到达顺序播放即可，
> 语音与文字不要求帧级对齐（合成有网络耗时）。

### 11.4 与「轮询 `GET /api/device/message/{id}`」的关系

- 老路径不变：`POST /api/device/message` + 长轮询仍然可用（`tts=1` 时整段合成回包）；
- 新路径只是把同一条回复**增量**推给设备；`reply_text` 现在会在生成过程中逐步更新，
  轮询侧看到的也是同一份内容（只是粒度粗一些）；
- H5 / Online 的 `GET /api/h5-chat/messages/{id}/events`（SSE）同样受益：正文变成真流式。

### 11.5 对外发放版本

给设备厂商的**独立对接文档**（可直接转发，不含内部实现）：`docs/device-voice-stream-vendor.md`，
内容 = §11 的对外表述（连接/鉴权、控制帧、事件表、真实抓包、HTTP 备用通道、计费、错误处置、设备伪代码）。
修改协议时两份要同步改。
