# 设备语音接入 · 流式回复与逐句语音 对接文档

| 项 | 值 |
|---|---|
| 文档版本 | v1.1（2026-10-10） |
| 适用对象 | 智能语音终端 / 嵌入式设备厂商 |
| 服务地址 | `https://bhzn.top`（必须 HTTPS / WSS） |
| 传输 | WebSocket（`wss://`）+ JSON 控制帧；音频上行可选 16bit PCM 二进制帧 |
| 兼容性 | 在 v1.0（实时流式识别）基础上新增 `ask` 流式问答；**老字段、老流程全部保留** |

本文档说明设备如何「一句话问出去」，并在模型还在生成时就收到**文字增量**与**逐句语音**。

---

## 1. 连接与鉴权

```
wss://bhzn.top/api/h5-chat/voice/session?device_token=dev_xxxxxxxx&installation_id=<可选>
```

- `device_token`：设备绑定后拿到的一次性返回、需存入 NVS/Flash 的长期 token；
  服务端用 `sha256(device_token)` 查绑定表，找到**绑定它的账号**，之后一切以该账号身份执行与扣费；
- `installation_id`：可选，多台设备同一账号时用于区分；
- 鉴权失败：服务端返回 `{"type":"error","message":"登录已失效，请重新登录后再试"}` 并以 **close code 4401** 关闭连接，设备应重新走配对绑定；
- App / H5 场景也可传 `?token=<用户 JWT>`，设备不需要。

连接成功后即可收发 JSON 帧；音频上行使用二进制帧（见 §3）。

---

## 2. 两种用法

| 用法 | 上行 | 下行 | 适用 |
|---|---|---|---|
| **A. 实时流式识别（v1.0）** | `start` + 16k/16bit 单声道 PCM 二进制帧 + `stop` | `partial` / `final`（边说边出字） | 设备自己带麦克风、要实时转写 |
| **B. 流式问答（v1.1，本文重点）** | `{"type":"ask","text":"…","tts":1}` | `reply_accepted` → `reply_delta`… → `reply_audio…` → `reply_done` | 文本已拿到（按键说话、识别结果、按钮指令），要边生成边播报 |

两种用法可共用一条连接：先 `start/stop` 拿到 `final`，再把它当 `text` 发 `ask`。

---

## 3. 控制帧（设备 → 云端）

| type | 字段 | 说明 |
|---|---|---|
| `ping` | — | 心跳，服务端回 `{"type":"pong"}`；建议 20~30s 一次 |
| `start` | — | 开始实时识别会话 |
| `stop` | — | 结束实时识别（发完最后一片音频后调用） |
| `ask` | `text`(必填) / `tts`(可选) / `session_id`(可选) | 提交一句话给 AI 调度助手，并要流式回复 |

`ask` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | string | 要问的内容，空字符串会返回 `error` |
| `tts` | `1/true/yes/on/y` 为开 | 开则逐句合成语音；不开只回文字增量 |
| `session_id` | string | 可选。缺省沿用该账号默认会话；同一会话多轮 = 连续对话 |

示例：

```json
{"type":"ask","text":"帮我看看今天有什么安排","tts":1}
```

规则：

- 同一条连接上再发 `ask` 会**取消上一条未完成的流**，以最新一条为准；
- 连接断开时未完成的流自动取消；
- 单条 `ask` 服务端最多跟进 **600 秒**，超时会以 `reply_done` + `"timeout":true` 收尾。

音频（二进制帧）格式：**16kHz / 16bit / 单声道 / 小端 PCM**，原始字节流，建议每片 40~200ms（1280~6400 字节）。
第一片前必须先发 `{"type":"start"}`，最后一片后发 `{"type":"stop"}`。

---

## 4. 事件（云端 → 设备）

### 4.1 识别类（用法 A）

| type | 关键字段 | 说明 |
|---|---|---|
| `listening` | `provider` | 识别通道已就绪 |
| `partial` | `text` | 中间结果（可能多次，会覆盖前一次） |
| `final` | `text` | 最终识别文本 |
| `intent` | `intent` 等 | 服务端对最终文本的意图判断（可选） |
| `pong` | — | 心跳应答 |
| `error` | `message` | 一般性错误（如未开始就 `stop`） |

### 4.2 流式问答类（用法 B，v1.1 新增）

按到达顺序：

| type | 关键字段 | 说明 |
|---|---|---|
| `reply_accepted` | `message_id` / `session_id` / `status` / `tts` | 已进入 AI 调度，后续事件都带这个 `message_id` |
| `reply_delta` | `seq`(从 1 递增) / `delta`(本次新增) / `text`(当前全文) / `chars` | **正文增量**；设备建议直接 `append(delta)` 上屏 |
| `reply_audio` | `index`(第几句) / `text` / `chars` / `credits` / `cached` / `url` / `abs_url` | 某一句合成完成，可直接 GET `url` 播放 |
| `reply_audio_error` | `index` / `text` / `error` | 该句合成失败（**文字不受影响**） |
| `reply_audio_full` | `segments` / `chars` / `url` / `abs_url` | 收尾：整段合并音频（按序拼接，可重播） |
| `reply_done` | `status` / `text` / `chars` / `segments` / `credits` / `audio_full_url` / `timeout` | 本条流结束（成功或失败） |
| `reply_error` | `message` / `message_id`(可能没有) | 提交失败 / 消息不存在 / 任务失败 |

约定（重要）：

1. **首字延迟**取决于上游模型与网络，通常 0.5~2s；服务端在模型产出过程中就落库并推送，不会等整段结束；
2. **逐句语音 ≥20 字的句子才单独合成**；更短的残句不单独合成，会并入 `reply_audio_full`；
3. **同一句只合成一次**：按「消息ID_句序号」缓存，重发/重连不会重复扣费；
4. `reply_audio_full` 是按序拼接已合成的分句音频（**不额外扣费**）；若整条回复没有任何 ≥20 字的句子，则改为整段合成一次；
5. 音频地址形如 `/api/device/audio/tts_xxxx.mp3`，**无需 token，设备直接 GET 即可播放**；`abs_url` 是带公网域名的完整地址（服务端能解析出公网基址时才给）；
6. `seq` 只在本次 `ask` 内递增；`index` 是句序号（本次 `ask` 内从 1 递增）；
7. 语音与文字**不保证帧级对齐**（合成有网络耗时），设备按到达顺序播放即可；
8. Markdown 标记（`**`、`#`、列表、链接等）在合成前会被自动清洗，不会念出来。

---

## 5. 时序（用法 B）

```
设备                                      云端
 |  {"type":"ask","text":"…","tts":1}
 |---------------------------------------->|
 |  {"type":"reply_accepted","message_id":"…"}          ← 已受理
 |<----------------------------------------|
 |  {"type":"reply_delta","seq":1,"delta":"好的，…"}     ← 边生成边回
 |<----------------------------------------|
 |  {"type":"reply_audio","index":1,"url":"/api/device/audio/…"}  ← 第 1 句可播
 |<----------------------------------------|
 |  {"type":"reply_delta","seq":2,"delta":"…"}          ← 正文继续
 |<----------------------------------------|
 |  {"type":"reply_audio","index":2,"url":"…"}          ← 第 2 句可播
 |<----------------------------------------|
 |  {"type":"reply_audio_full","url":"…_full.mp3"}      ← 整段音频（可重播）
 |<----------------------------------------|
 |  {"type":"reply_done","status":"completed","text":"…"}          ← 本轮结束
 |<----------------------------------------|
```

真实抓包（生产环境实测，已脱敏；`+x.xxs` 为相对 ask 发出的时间）：

```
[+0.04s] <- reply_accepted message_id=0e7ac7d18420 status=pending tts=True
[+1.55s] <- reply_delta seq=1 +2 chars (total=2)
[+2.05s] <- reply_delta seq=2 +127 chars (total=129)
[+7.02s] <- reply_audio index=1 chars=21 credits=1.0 url=/api/device/audio/tts_0e7ac7d18420…_1.mp3
[+10.16s] <- reply_audio index=2 chars=23 credits=1.0 url=/api/device/audio/tts_0e7ac7d18420…_2.mp3
[+13.23s] <- reply_audio index=3 chars=26 credits=1.0 url=/api/device/audio/tts_0e7ac7d18420…_3.mp3
[+16.86s] <- reply_audio index=4 chars=55 credits=1.1 url=/api/device/audio/tts_0e7ac7d18420…_4.mp3
[+16.87s] <- reply_audio_full segments=4 url=/api/device/audio/tts_0e7ac7d18420…_full.mp3
[+16.87s] <- reply_done status=completed chars=129 segments=4 credits=4.1 timeout=False
```

---

## 6. HTTP 备用通道（无 WS 或弱网时）

| 接口 | 说明 |
|---|---|
| `POST /api/device/message` | body `{"text":"…","tts":1,"session_id":"…"}`，提交一句话，返回 `message_id` |
| `GET /api/device/message/{message_id}?wait=30&tts=1` | 长轮询拿回复；`reply_text` 非空或状态终态即返回；`tts=1` 时带 `reply_audio_url`（整段合成） |
| `POST /api/device/audio` | 上传整段音频（wav/mp3/m4a…，≤20MB），服务端转写后送 AI |
| `GET /api/device/audio/{name}` | 取音频文件（无需 token） |

> 设备轮询看到的 `reply_text` 与 WS 的 `reply_delta` 是同一条回复：v1.1 起 `reply_text` 也是**增量**更新的，只是轮询粒度更粗。

---

## 7. 计费

| 项 | 规则 |
|---|---|
| 文字问答 | 由**绑定该设备的账号**按平台对话计费规则扣算力 |
| 逐句/整段语音 | 同上账号扣费；同句命中缓存不重复扣；单句最低 1 分 |
| 余额不足 | 语音合成失败会返回 `reply_audio_error`（`算力不足：…`），**文字照常返回**；提交阶段余额不足则 `reply_error` |

---

## 8. 错误与处置

| 现象 | 含义 | 设备侧处置 |
|---|---|---|
| close code **4401** | `device_token` 失效/解绑 | 重新走绑定流程，拿到新 token |
| `{"type":"error","message":"ask 缺少 text"}` | `ask` 没带文本 | 检查上行 JSON |
| `reply_error` | 提交失败 / 消息不存在 / 任务失败 | 提示用户重试；保留已收到的 `text` |
| `reply_audio_error` | 该句语音合成失败 | 只用文字；不要重试整条流（服务端已按句缓存成功部分） |
| `reply_done.timeout=true` | 600s 内未跑完 | 用 `text` 显示已知内容，可让用户追问 |
| 连接被关闭 | 断网/NAT 超时 | 指数退避重连；重连后重发 `ask`（同句音频命中缓存不重复扣费） |

---

## 9. 设备侧参考实现（伪代码）

```c
ws_connect("wss://bhzn.top/api/h5-chat/voice/session?device_token=" DEVICE_TOKEN);

/* 文本问答 + 语音播报 */
ws_send_text("{\"type\":\"ask\",\"text\":\"今天有什么安排\",\"tts\":1}");

while (ws_connected) {
    msg = ws_recv_json(&ok);
    if (!ok) { backoff_reconnect(); break; }

    if      (strcmp(msg.type, "reply_accepted") == 0) { ui_clear(); playing = 0; }
    else if (strcmp(msg.type, "reply_delta")    == 0) { ui_append(msg.delta); }
    else if (strcmp(msg.type, "reply_audio")    == 0) { audio_enqueue(msg.abs_url[0] ? msg.abs_url : server_base + msg.url); }
    else if (strcmp(msg.type, "reply_audio_error") == 0) { log_warn(msg.error); }
    else if (strcmp(msg.type, "reply_audio_full")  == 0) { last_full_url = msg.url; }   /* 需要重播时用 */
    else if (strcmp(msg.type, "reply_done")     == 0) { ui_finish(msg.status); break; }
    else if (strcmp(msg.type, "reply_error")    == 0) { ui_error(msg.message); break; }
    else if (strcmp(msg.type, "pong")           == 0) { /* 心跳正常 */ }
}
```

音频播放建议：

- 收到 `reply_audio` 就入队按序播放，**不要等 `reply_done`**；
- 语音队列播放速率低于生成速率时会自然堆积，这是正常的；
- 用户点「重播」时优先用 `reply_audio_full.url`（整段），没有则回退到分句队列重播。

---

## 10. 版本与变更

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-09 | 实时流式识别（`start/stop` + PCM）、`final`、`intent` |
| v1.1 | 2026-10-10 | 新增 `ask` 流式问答：`reply_accepted/reply_delta/reply_audio/reply_audio_error/reply_audio_full/reply_done/reply_error`；服务端回复文本改为增量落库（轮询通道同步受益） |

需要自测脚本或联调协助，请联系对接人。
