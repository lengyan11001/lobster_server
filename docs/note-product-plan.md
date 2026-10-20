# 必火听记录 · 能力规划（对照元梦录音笔记）

> 今天的表达，明天的资产。

站点：`note.bhzn.top`（代码：`backend/app/note_main.py` + `note_static/`）
账号：复用 admin / manage 同一套（`/auth/*`）
录音与转写：复用 H5 的 `backend/app/api/h5_recorder.py`（`/api/h5/recorder/*`）

## 一、能力矩阵（现状 → 落地方式 → 阶段）

| 功能模块 | 主要能力 | 现状 | 落地方式（复用/新增） | 阶段 |
|---|---|---|---|---|
| 智能录音 | 手机/网页录音、设备录音 | ✅ 已有 | 网页 MediaRecorder + 上传 `/api/h5/recorder/files`；设备侧沿用 H5 录音卡/客户端 | P0 |
| AI 语音转写 | 语音转文字、区分发言人、多语言识别 | ✅ 已有 | `h5_recorder` 后台转写；`transcript_text` + `segments[].speaker`；`/files/{id}/speaker` 改名 | P0 |
| AI 内容分析（摘要/要点） | 总结、关键结论、待办线索 | ✅ 已有 | `summary_text` + `key_points`（LLM 已有 prompt） | P0 |
| 内容导出 | TXT / Markdown（后续 DOCX/PDF/SRT） | 🟡 本次落地 | 前端由详情数据生成 TXT/MD；DOCX/PDF 走服务端导出接口 | P0 |
| AI 知识库 | 录音/文本/资料沉淀、长期可检索 | 🟡 部分 | 已有 `OpenClawMemoryDocument` + 录音写入记忆文件（`/files/{id}/memory`）；补「知识库」页与检索 | P1 |
| 待办抽取 | 负责人/截止时间/行动项 | ⬜ 待建 | 在 `_analyze` 的 JSON 里增加 `todos[]`；落库 `recorder_todos` | P1 |
| 思维导图 | 结构化大纲/脑图 | ⬜ 待建 | 分析结果新增 `outline`（树形），前端用轻量渲染或导出 OPML/Markdown | P1 |
| 翻译（录音后） | 全文/逐段翻译 | ⬜ 待建 | 新增 `/api/note/translate`（复用 `_call_llm`），结果存 `recorder_translations` | P1 |
| AI 智能体 | 围绕录音/知识库持续提问 | 🟡 部分 | 复用 Mastra / H5 chat 的 `readPersonalMemory`；新增「听记录」会话上下文（录音 id 列表） | P1 |
| 实时转译 | 边说边转、边说边译 | ⬜ 待建 | 流式 STT（现有语音网关）+ SSE；前端分轨显示原文/译文 | P2 |
| 对话翻译 | 面对面跨语言 | ⬜ 待建 | 双栏对话 UI + 逐句 STT→翻译→TTS；复用翻译通道 | P2 |
| 深度研判 / 联网查证 | 结合外部信息核验 | ⬜ 待建 | LLM + 搜索能力（已有联网检索能力可复用），输出「结论/依据/来源」 | P2 |
| 流量采矿 | 从对话里挖选题/卖点/话术 | ⬜ 待建 | 基于 `key_points` + 行业 Agent prompt，产出选题/脚本草稿 | P2 |
| 数据授权 | 导出授权、第三方数据使用 | ⬜ 待建 | 授权记录表 + 导出水印/范围控制 | P2 |

## 二、数据模型（新增）

```sql
-- 分析结果（一次录音一份，JSON 便于扩展）
ALTER TABLE recorder_audio_records ADD COLUMN analysis JSON;      -- {todos:[], outline:{}, insights:[]}
ALTER TABLE recorder_audio_records ADD COLUMN language TEXT;      -- 识别语言
ALTER TABLE recorder_audio_records ADD COLUMN translated_text TEXT;
ALTER TABLE recorder_audio_records ADD COLUMN translate_lang TEXT;

-- 待办（可勾选、可指派）
CREATE TABLE recorder_todos (
  id SERIAL PRIMARY KEY, user_id INT NOT NULL, record_id INT NOT NULL,
  title TEXT NOT NULL, owner TEXT, due_at TIMESTAMP, status VARCHAR(16) DEFAULT 'open',
  created_at TIMESTAMP DEFAULT now()
);

-- 导出记录（授权/审计）
CREATE TABLE note_exports (
  id SERIAL PRIMARY KEY, user_id INT NOT NULL, record_id INT NOT NULL,
  format VARCHAR(16), scope VARCHAR(32), created_at TIMESTAMP DEFAULT now()
);
```

## 三、API 规划

| 方法 | 路径 | 说明 | 阶段 |
|---|---|---|---|
| GET | `/api/h5/recorder/files/{id}` | 已有；补 `analysis/translated_text/language` | P1 |
| POST | `/api/h5/recorder/files/{id}/analyze` | 重新分析（摘要/要点/待办/大纲） | P1 |
| POST | `/api/h5/recorder/files/{id}/translate` | `{target_lang}` → 全文/逐段翻译 | P1 |
| GET/POST | `/api/note/todos` | 待办列表 / 勾选完成 | P1 |
| GET | `/api/note/kb` | 知识库列表（录音/文档/记忆） | P1 |
| POST | `/api/note/ask` | 围绕选中录音/知识库提问（流式） | P1 |
| GET | `/api/h5/recorder/files/{id}/export?format=md|txt|docx|pdf|srt` | 服务端导出 | P1 |
| POST | `/api/note/live/transcribe` | 实时转译（SSE） | P2 |
| POST | `/api/note/translate/dialog` | 对话翻译（逐句） | P2 |
| POST | `/api/note/verify` | 联网查证（结论+来源） | P2 |

## 四、界面信息架构

```
首页
├─ 今日 AI 概览（录音/已整理/待整理/要点）
├─ 麦克风录音 + 上传
├─ 能力入口：转写 · 翻译 · 待办 · 思维导图 · 知识库 · 导出 · 智能体
└─ 最近录音

录音记录
├─ 搜索 / 状态筛选
└─ 详情：摘要 · 要点 · 待办 · 发言人逐字稿 · 翻译 · 导出 · 加入知识库

我的
├─ 账号 / 品牌 / 用量
├─ 知识库
├─ 导出与授权记录
└─ 退出登录
```

## 五、路线图

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0（本次） | 三页签、录音/上传/转写/摘要/要点、发言人展示、TXT/MD 导出 | 一条录音从录到导出全链路可用 |
| P1 | 待办抽取、思维导图、翻译、知识库页、智能体问答、DOCX/PDF/SRT 导出 | 详情页五件套齐全，知识库可检索 |
| P2 | 实时转译、对话翻译、联网查证、流量采矿、授权与审计 | 会议/跨境/内容三类场景可演示 |

## 六、与竞品的差异点（保留）

1. 首页是「AI 今天替你看懂了什么」，不是录音列表（已落地概览）。
2. 长期记忆：录音 → 摘要/要点 → 知识库 → 跨录音问答（P1 起）。
3. 结果模板：老板日报 / 销售教练 / IP 选题等 Agent 模板（P2 起，按模板售卖）。
