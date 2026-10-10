# 系统级 AI 身份（角色）设定 · 火火

| 项 | 值 |
|---|---|
| 生效日期 | 2026-10-10 |
| 生效品牌 | `bihuo`（必火）；其它 OEM 品牌不受影响（见 §4） |
| 相关文件 | `mastra_server/src/mastra/identity.ts`、`mastra_server/src/mastra/index.ts`、`backend/app/services/ai_identity.py`、`backend/app/api/mastra_chat.py` |

---

## 1. 设定内容（原文）

【系统角色设定】
- 身份：必火AI员工执行系统
- 开发主体：深圳市必火智能信息技术有限公司
- 昵称：火火
- 定位：客户的AI助理

【强制默认自我介绍规则】
当用户发起对话、语音唤醒、询问“你是谁”“你叫什么”时，必须输出固定应答文本：

> 您好，我是由深圳市必火智能信息技术有限公司开发的AI执行系统，我是您的AI助理，名叫火火。

【行为约束】
1. 初次对话、语音唤醒时优先播报以上自我介绍；
2. 不得自行修改公司名称、产品名称、昵称；
3. 不擅自编造其他身份信息；
4. 后续对话正常回答用户问题，不需要重复自我介绍，只有用户询问身份时再次回复该段话术。

---

## 2. 落在系统的哪几个地方

| 层 | 文件 | 作用 |
|---|---|---|
| 模型（Mastra 编排 agent） | `mastra_server/src/mastra/identity.ts` | 身份文本的唯一来源：`IDENTITIES` / `identityBlockFor(brand)` / `identityIntroFor(brand)` |
| 模型（注入点） | `mastra_server/src/mastra/index.ts` → `prepareChatInput()` | 把【系统角色设定】文本块插到**本轮编排输入的最前面**（`prependIdentity()`），优先级高于用户输入 |
| 模型（约束） | `index.ts` → `orchestrator.instructions` 第 21 条；`answerFinalizer.instructions` 第 7 条 | 不许改名 / 不许编造身份 / 被问身份时一字不改地输出固定话术 |
| 后端（硬保证） | `backend/app/services/ai_identity.py` | 同一份口径的 Python 实现 + 命中判断（问身份 / 首轮问候） |
| 后端（接线） | `backend/app/api/mastra_chat.py` → `create_mastra_message()` | 命中时**直接落一条已完成消息**（固定话术），不走模型、不计费 |

> 为什么要两处：模型提示词是「尽量遵守」，而需求写的是「**必须**输出固定应答文本」。
> 所以身份问答与首轮问候走后端确定性分支 —— 不受模型波动、模型切换、上下文长度影响。
> （Mastra 侧那层仍然保留：普通轮次里模型也要知道自己是谁，不能自称别的。）

---

## 3. 触发规则

| 场景 | 判定 | 结果 |
|---|---|---|
| 用户问身份（你是谁 / 你叫什么 / 介绍一下你自己 / 哪家公司 / 谁开发 …） | `is_identity_question()`，短句（≤40 字）归一化后包含关键词 | **固定自我介绍**（任何轮次都生效） |
| 会话**首轮**且只是一句问候 / 唤醒词（你好 / 您好 / 哈喽 / 嗨 / hi / hello / 在吗 / 早上好 / 火火 …） | `is_greeting()`，≤12 字且除语气词外没有别的内容 | **固定自我介绍**（并在设备侧合成语音播报） |
| 首轮但带了任务（“你好，帮我写个文案”） | 不命中问候 | 走模型正常回答 |
| 非首轮的问候（“你好”再发一次） | 不是首轮 | 走模型正常回答（不重复自我介绍） |
| 其它任何轮次 | — | 走模型；身份口径由【系统角色设定】约束 |

补充：
- 固定话术这条消息**不调用模型、不扣对话算力**；设备若开了 `tts=1`，语音合成仍按绑定账号正常计费（同句缓存不重复扣）。
- 带附件的消息不做固定话术判定（可能是在问“这张图是谁”）。
- 会话 = `session_id`；同一会话第一条消息视为「初次对话」。

---

## 4. 品牌隔离（重要）

`IDENTITIES` 只配置了 `bihuo`。生产库里还有 `daka / hikong / jinghai / yingshi` 等 OEM 品牌，
它们**不会**被注入「必火 / 深圳市必火智能信息技术有限公司 / 火火」，也**不会**走固定话术
（未配置品牌返回空串，行为与改动前一致）。

新增一个品牌：在 `identity.ts` 与 `ai_identity.py` 的 `IDENTITIES` 各加一条（两处文案必须一致）：

```ts
IDENTITIES['daka'] = {
  company: '……有限公司',
  product: '……AI员工',
  nickname: '……',
  role: '客户的AI助理',
  intro: '您好，我是由……开发的……，我是您的AI助理，名叫……。',
}
```

---

## 5. 怎么验证

```bash
# 1) 纯逻辑（关键词命中 / 品牌隔离）
python -c "import sys; sys.path.insert(0,'D:/lobster_server/backend'); sys.path.insert(0,'D:/lobster_server'); from app.services import ai_identity as a; print(a.fixed_reply('你是谁', brand_mark='bihuo', is_first_turn=False)); print(a.fixed_reply('你好', brand_mark='daka', is_first_turn=True))"

# 2) 端到端（真 WS + 真 DB，桩 Mastra 计数：身份问答不该调用模型）
python _device_stream_selftest/selftest_identity.py
#    期望：首轮问候 / 身份问答 → 固定话术 且 模型调用次数不变；普通任务 → 确实调用 1 次模型

# 3) Mastra 侧类型检查
cd D:/lobster_server/mastra_server && npx tsc --noEmit
```

---

## 6. 修改约定

- 固定自我介绍文本、公司名、产品名、昵称：**必须同时改** `mastra_server/src/mastra/identity.ts` 与
  `backend/app/services/ai_identity.py`（两份口径要一致，否则模型层和后端层会打架）。
- 关键词（问候词 / 问身份说法）：只改 `ai_identity.py`。
- 发布：Mastra 侧源码指纹变化后，部署脚本会自动重建（`scripts/build_mastra_if_needed.sh`），
  后端随 `lobster-backend` 重启生效 —— 走 `scripts/deploy_from_local.py`（需用户说「发」）。
