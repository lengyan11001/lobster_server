# manage · AI 调度与工作安排流程（含提示词）

> 一句话：**老板只输入目标，其余（阶段、任务、人员、要求、时间节点）全部由 AI 排好**，
> 老板只做「改一改 + 确认」。AI 走的是现有服务端统一调度通道，不新造模型链路。

---

## 1. 走哪条模型通道（复用，不自建）

| 用途 | 通道 | 模型选择 |
|---|---|---|
| 规划 / 工作安排 / 复盘（单轮结构化产出） | `POST {AUTH_SERVER_BASE}/api/sutui-chat/completions` | 入参 `model` 由服务端决定：客户端只传 `lobster_orchestration_sutui_chat_model` → `lobster_default_sutui_chat_model` → `deepseek-chat` |
| 多步编排 / 需要工具调用（查员工、查产品、建任务） | Mastra 编排服务（`lobster-mastra.service`） | `LOBSTER_MASTRA_MODEL`，默认 `openai/gpt-5.6-sol` |
| 媒体/长任务 | 现有 scheduled-task 链路 | 同 8 节 |

服务端自带降级链（`sutui_chat_proxy._sutui_chat_model_candidates`）：
主 DeepSeek（V4.1 Flash）→ 文本兜底 → change2pro → yyapi → 多模态兜底 → 入站模型，
熔断的模型自动跳过。**manage 不自己写降级逻辑**，只负责发一次请求、处理好"模型返回不合格"的情况。

---

## 2. 整体流程（8 步）

    ① 建档        组织架构（人 + 岗位 + 级别）/ 产品
    ② 输入目标    老板填：目标 + 周期 + 参与人 + 产品
    ③ 上下文装配  服务端把组织结构、产品、历史项目、客户、虚拟员工在线状态拼成 JSON
    ④ AI 总规划   → phases / tasks / milestones / risks / kpis
    ⑤ AI 工作安排 对每个节点产出：责任人 + 要求(交付标准) + 时间节点  ← 关键：不等老板填
    ⑥ 校验与自愈  结构校验 + 人员/时间冲突校验，不合格自动重试（≤2 次）
    ⑦ 达成条件    AI 以"项目能不能做成"反推所需条件（人/钱/设备/素材/渠道/资质/时间），
                  给出补位动作（招聘 / 增配虚拟员工 / 加预算 / 外包 / 缩范围），老板逐条采纳
    ⑧ 草稿/确认   老板改任意节点 → 存草稿 → 确认（确认后才可下发）
    ⑨ 下发与回流  虚拟员工 → 定时任务；人 → 我的任务；进度自动回填，触发再规划与条件重算

④⑤ 是本系统的核心，下面给完整提示词。

---

## 3. 上下文装配（③ 的产物，直接作为 user 消息）

```json
{
  "project": {"name":"", "goal":"", "success_criteria":"", "start_at":"", "end_at":"", "period_days":90},
  "products": [{"name":"抖音获客代运营","price":39800,"cycle_days":90,"deliverable":"30条短视频+4场直播"}],
  "members": [{"membership_id":3,"name":"林珂","roles":[{"code":"sales","level":"p3"}],"remark":"华南大区","load_pct":92}],
  "virtual_employees": [{"id":1,"name":"虚拟员工·抖音A","online":true,"capabilities":["douyin.publish","douyin.dm","douyin.collect"],"last_seen":"2026-09-14T09:20:00+08:00"}],
  "customers": [{"id":11,"name":"华创科技","stage":"won","amount":39800,"owner":"林珂"}],
  "history_projects": [{"name":"某品牌抖音0-1","result":"3个月成交28万","phases":4,"duration_days":88}],
  "constraints": {"max_parallel_tasks_per_person":3, "workday_hours":8, "timezone":"Asia/Shanghai"}
}
```

---

## 4. 提示词 P1：项目总规划

**system**

```
你是「AI 项目总监」，为一家中小企业把老板的目标拆成可执行的项目计划。
你只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块、不要多余文字。

硬性要求：
1. 阶段数 3-5 个，阶段之间时间连续且不重叠，且完整覆盖项目周期（不允许留空档）。
2. 每个阶段 2-5 个任务；任务必须可交付、可验收，不能是"推进一下"这种虚动词。
3. 每个任务必须给：title、detail、owner(责任人)、start_at、end_at、kpi、deliverable、depends_on。
4. owner 只能从输入的 members / virtual_employees 里选，禁止编造人名；
   机械重复、批量执行、需要设备在线的工作优先指派给 virtual_employees。
5. 时间用 YYYY-MM-DD；同一人的并行任务不超过 constraints.max_parallel_tasks_per_person。
6. 必须参考 history_projects 的实际工期，不要给出明显不现实的排期。
7. 至少 2 个里程碑、3 条风险；kpis 2-4 条且必须可量化（带数字）。
```

**user**：第 3 节的上下文 JSON + `{"task":"generate_project_plan"}`

**输出 schema**

```json
{
  "summary": "一句话总览",
  "phases": [
    {"title":"阶段一 · 账号与内容基建","start_at":"2026-09-15","end_at":"2026-10-05","goal":"...",
     "tasks":[{"title":"","detail":"","owner":{"kind":"human|virtual_employee","membership_id":3,"ai_employee_id":1,"role_code":"sales"},
               "start_at":"","end_at":"","weight":3,"kpi":"","deliverable":"","depends_on":[]}]}
  ],
  "milestones":[{"title":"","at":"","criteria":""}],
  "risks":[{"title":"","impact":"","mitigation":""}],
  "kpis":[{"name":"","target":"","measure":""}]
}
```

---

## 5. 提示词 P2：工作安排（人员 + 要求 + 时间节点）

> 每个节点单独调一次（可并发），拿到的就是安排表那一行。**这就是"开始就让 AI 来安排"的那一步。**

**system**

```
你是「AI 项目经理」，负责把一个项目节点写成一句可执行的工作安排。
你只输出一个 JSON 对象，不要解释、不要代码块。

必须同时给出三样东西，缺一不可：
1. owner  —— 责任人。只能从给定候选人里选；候选人里带 online=true 的虚拟员工，
   凡是"批量、重复、需要设备/账号在线"的活优先给它；要人的活按 roles/level 和 load_pct 选。
2. requirement —— 要求/交付标准。必须写清"做成什么样才算完成"，包含：
   数量或频率（如 ≥15 条/天）、质量线（如 CTR 提升 ≥20%）、验收物（如报表/截图/文档）。
   禁止写"做好一点""尽快完成"这类无法验收的话。
3. timeline —— 时间节点。给 start / end（YYYY-MM-DD），必须落在节点所属阶段区间内，
   且考虑责任人已有任务（不要让人并行超过 N 件）。

约束：
- 只输出这三个字段 + 一个 20 字以内的 reason（为什么这样安排），不要输出其它内容。
- 不要编造候选人以外的人；不要给虚拟员工安排需要人类判断（谈判、创意决策）的活。
```

**user**

```json
{
  "task":"arrange_node",
  "project":{"name":"","goal":"","start_at":"","end_at":""},
  "phase":{"title":"阶段二 · 放量获客","start_at":"2026-10-06","end_at":"2026-11-10"},
  "node":{"title":"投流素材 A/B 测试","detail":"每 2 周产出 4 组素材对照","kpi":"CTR 提升 ≥20%","weight":3},
  "constraints":{"max_parallel_tasks_per_person":3,"timezone":"Asia/Shanghai"},
  "candidates":[
    {"kind":"human","membership_id":4,"name":"周研","roles":[{"code":"content","level":"p2"}],"load_pct":96},
    {"kind":"virtual_employee","ai_employee_id":1,"name":"虚拟员工·抖音A","online":true,"capabilities":["douyin.publish"]}
  ]
}
```

**输出**

```json
{"owner":{"kind":"virtual_employee","ai_employee_id":1,"name":"虚拟员工·抖音A"},
 "requirement":"每 2 周产出 4 组素材对照，每组含 3 个封面 2 个开头；CTR 提升 ≥20% 的素材加量；交付：素材对照表 + 投放后台截图",
 "timeline":{"start":"2026-10-06","end":"2026-10-20"},
 "reason":"批量素材测试优先用在线虚拟员工，避免内容组过载"}
```

---

## 6. 提示词 P3：节点细化（任务太大时自动拆）

**system**

```
你是「AI 执行教练」。给定一个任务，判断它是否可以在 3 个工作日内被一个人完成：
- 可以：原样返回 {"split": false}
- 不可以：拆成 2-5 个子任务，每个子任务都必须能被单独指派和验收
只输出 JSON，不要解释。
```

**输出**

```json
{"split": true, "subtasks": [{"title":"","requirement":"","estimate_days":2,"depends_on":[]}]}
```

---

## 7. 提示词 P4：风险与复盘（周期内自动跑）

**system**

```
你是「AI 经营参谋」。输入是项目当前状态（节点进度、逾期项、工作记录、客户与回款）。
输出 JSON：
{"health":"good|watch|risk","headline":"一句话结论（<=30字）",
 "findings":[{"title":"","evidence":"用输入里的数字说话","impact":"","action":"下一步具体动作","owner_role":"sales"}],
 "next_week":[{"what":"","why":"","when":""}]}
禁止空话；每条 finding 必须引用输入里的数字。
```

---

## 8. 提示词 P5：重规划（带老板批注）

**system**

```
你是「AI 项目总监」。输入是上一版完整计划 JSON + 老板的修改要求。
要求：
1. 严格保留上一版里**已确认且未被要求改动**的部分（阶段结构、已指派的人、已排好的时间）。
2. 只改动老板明确要求改动的地方，以及为满足新要求必须连带调整的排期。
3. 已被执行（有工作记录/进度>0）的任务不得删除，只能改期或改责任人，并在 change_reason 里说明。
4. 输出完整的新版计划 JSON（同 P1 schema），外加 "changes" 数组：
   {"type":"added|removed|retimed|reassigned","node":"节点标题","from":"","to":"","why":""}
只输出 JSON。
```

**user**：`{"previous_plan": {...上一版...}, "instruction":"首周必须完成 3 场直播；财务只参与结算"}`

---

## 9. 校验与自愈（⑥）

服务端拿到 AI 输出后逐条校验，**不合格就带着错误信息重试（≤2 次），仍不合格则报错退出，不做静默兜底**：

| 校验 | 不通过时 |
|---|---|
| JSON 可解析 + schema 必填齐全 | 回灌错误重试 |
| owner 必须在候选人集合内 | 回灌「只能用这些人」重试 |
| 阶段时间连续、覆盖项目周期 | 回灌冲突区间重试 |
| 子任务时间不越出阶段区间 | 回灌重试 |
| 单任务 >3 工作日 | 自动触发 P3 拆分 |
| 同一人并行任务数超限 | 自动换候选人重排 |
| 责任人/要求/时间节点任一缺失 | **不允许进入"待确认"**，标记为待补充 |

---

## 10. AI 可以调用的工具（多步编排时走 Mastra）

| 工具 | 作用 |
|---|---|
| `list_members(company_id)` | 拿组织架构、岗位、级别、当前负载 |
| `list_virtual_employees(company_id)` | 拿槽位与**在线状态**、能力标签 |
| `list_products(company_id)` | 拿产品与交付物 |
| `search_customers(company_id, keyword)` | 拿客户与阶段 |
| `get_history_projects(company_id, similar_to)` | 拿历史项目工期与结果 |
| `create_scheduled_task(...)` | 给虚拟员工下发任务（确认后才调用） |
| `get_run_detail(run_id)` | 拿执行明细与失败原因（复用 targets_detail） |

工具调用边界：**确认之前，AI 只能读，不能建任务**。

---

## 11. 与在线系统打通：组织架构 ↔ 原系统

加人时有两条路，都要接：

**不设"导入"这一步：manage 与原系统共用同一份用户数据（`users` 表），直接引用即可。**

- 老板点「添加成员」→ 按 手机号 / 邮箱 / 微信号 / 企业微信 userid **精确查找** → 选中即绑定 `user_id`，
  同一个人不产生第二份账号，登录即通（同一套 JWT）。
- **不默认列出任何人**：只有老板主动添加的人才会出现在组织架构里（不做全量列举，避免噪音与越权观感）。
- 原系统里没有的"纯记录人员"（如外包、兼职）：只建 `m_membership` 且 `user_id` 为空，
  不参与登录，仅作为工作安排上的责任人。

**在线设备 = 虚拟员工**（自动生成，不用手工录入）：

- 数据源：`UserInstallation` / `InstallationSlotOwner` / `H5ChatDevicePresence`（心跳）。
- 规则：该用户名下任一设备最近 `ONLINE_WINDOW`（默认 5 分钟）内有 presence 心跳 → 该槽位
  在组织架构里显示为 **在线虚拟员工**；超过窗口显示「离线」但仍然存在（可派活，等上线执行）。
- 展示：虚拟员工与人并列在组织架构树下（或按岗位分组），带在线状态点、能力标签、当前负载。
- 派活：只有「在线」的虚拟员工可以立即下发；离线可排期，客户端上线后自动领取
  （现有 `scheduled-tasks/pending` 机制本身就支持）。

---

## 12. 落库与审计

- AI 每次产出都落 `m_plan.raw_output`（原始 JSON）与 `m_plan.input_snapshot`（输入），**可复现**。
- `m_ai_call_log(id, company_id, scene, model, prompt_tokens, completion_tokens,
   latency_ms, ok, error, retry_of, created_at)`：便于看成本与失败率。
- 确认动作写 `m_audit_log`。

---

## 13. 提示词 P6：达成条件（最重要的一条 —— AI 要以「把项目做成」为目标思考）

> 老板给的资源永远是"当前有的"，不是"做成的必要条件"。
> AI 的任务是**先假设项目必须成功**，再反推还缺什么，而不是在给定资源里将就排期。

**system**

```
你是「AI 项目达成官」。你的唯一目标：让这个项目真的做成，而不是把任务排得好看。

思考方式（必须遵守）：
1. 先假设目标必须达成，反推「达成的必要条件」：人力、资金、设备与虚拟员工、素材与工具、
   渠道与关系、资质与合规、时间窗口，共 7 类。
2. 对每个条件给出四件事：需要的量、现状、缺口、对目标的影响（用数字说，比如"延期约 11 天"
   "私信转化率低于 8%"）。
3. 如果判断现有资源不足以达成目标，必须主动提出补位动作，允许超出老板已给的资源范围：
   - 招人（岗位 / 级别 / 人数 / 建议到岗时间 / 为什么非招不可）
   - 增配虚拟员工槽位（要什么能力、几个、何时可用）
   - 加预算 / 调预算（多少、什么条件触发）
   - 外包或合作（哪一段适合外部做）
   - 缩小范围或顺延目标（说清代价）
4. 不要只报告问题：每个缺口都要给"动作 + 谁来做 + 什么时候"。
5. 不要编造数据；输入里没有的用推断，并标注 confidence（high/medium/low）。
6. 只输出 JSON，不要解释、不要 markdown。

输出 schema：
{
  "verdict": {"achievable": true, "confidence": "medium",
              "headline": "一句话：按现状能达成 / 达成有风险 / 按现状达不成",
              "biggest_risk": ""},
  "conditions": [
    {"category": "people|money|device|material|channel|compliance|time",
     "title": "内容产能",
     "need": "≥15 条/天",
     "now": "1 人 p2，负载 96%",
     "gap": "缺 1 人",
     "impact": "阶段二延期约 11 天，成交目标后移",
     "severity": "high|medium|low",
     "actions": [
       {"type": "hire|slot|budget|outsource|scope|owner_action",
        "what": "招聘 1 名内容剪辑 p2", "why": "", "when": "2026-09-25",
        "cost": "¥9–12k/月", "confidence": "high", "owner_role": "hr"}
     ]}
  ],
  "if_nothing_changes": {"target_delta": "月成交 30 万 → 约 21 万", "reason": ""}
}
```

**user**：`{"task":"success_conditions", "project":{...}, "plan":{...当前计划...},
"resources":{人/钱/设备/素材现状...}, "history_projects":[...]}`

**服务端处理**：`conditions[].actions[]` 落到 `m_condition_action`；
老板「采纳」后按 `type` 触发对应动作——`hire → m_hire_request`、
`slot → m_ai_employee`（新增槽位）、`budget → m_expense(预算)`、
`scope → 回收并重算计划时间`。未采纳的保留在条件页作为风险提示。

**界面**：条件页按 7 类各一张卡：需要 / 现状 / 缺口（含 severity 色）/ 影响 + 一行一个「采纳」按钮；
顶部一句话结论（按现状能达成 / 有风险 / 达不成），点开可看 `if_nothing_changes` 的推演。

---

## 14. 定时项目日检（每天 09:00 / 17:00）

老板要的不是"随时能查"，而是**每天两次主动告诉他项目怎么样了**。

### 14.1 两次的分工

| 时间 | 定位 | 产出重点 |
|---|---|---|
| **09:00 早盘** | 今天该怎么打 | 今日重点动作、可能延期项、需要老板拍板的事项 |
| **17:00 收口** | 今天打得怎么样 | 干成了什么（用数字）、没干成的原因、明天怎么改、连续无响应的责任人 |

### 14.2 调度与成本控制（重要）

1. **先算后问**：服务端先跑**规则聚合**（进度偏差、逾期任务、负载、回款、在线设备离线时长），
   把「无变化 / 全部正常」的项目直接筛掉，**只把异常项和变化项交给模型**——
   否则每天两次全量分析，token 成本会失控。
2. **幂等**：同一公司同一时段只跑一次（幂等键 `company_id + slot(0900|1700) + date`），
   失败可重试但不会重复推送。
3. **降噪**：只推有变化的项目；但**带「需决策」的项目一定推**（宁多勿漏）。
4. **节奏**：09:00 与 17:00 各一次，时区按公司配置（默认 Asia/Shanghai）；跑完写 `m_project_checkup`。

### 14.3 提示词 P7：项目日检

**system**

```
你是「AI 项目体检官」，每天两次给老板做项目体检。你的标准是"项目能不能按时做成"。

输入是每个项目的一段结构化状态（进度、偏差、逾期、负载、回款、工作记录、上次体检结论），
以及本次是早盘(0900)还是收口(1700)。

要求：
1. 结论必须用数字说话，禁止"进展顺利""继续努力"这类空话。
2. 早盘：只讲"今天要做什么"与"需要老板拍板什么"；收口：只讲"今天实际干成什么"与"明天怎么改"。
3. 与上次体检对比：指出变好/变差的具体项（changes_since_last）。
4. 建议必须可执行：做什么、谁来做、什么时候之前（due）。
5. 只有当"不决策就会影响目标达成"时，才提 decisions，并给推荐选项。
6. 数据不足时把 confidence 标低，不要编造数字。
7. 只输出 JSON 数组，每个项目一个对象，不要解释、不要 markdown。

输出 schema（数组元素）：
{
  "project_id": 1,
  "health": "good|watch|risk",
  "headline": "一句话结论（≤30字，带数字）",
  "evidence": ["用来支撑结论的数字，2-4 条"],
  "suggestions": [{"what":"","why":"","owner_role":"","due":"2026-09-14T18:00"}],
  "decisions": [{"question":"","options":["A","B"],"recommend":"A","impact_if_ignored":""}],
  "changes_since_last": {"better":[""],"worse":[""]},
  "confidence": "high|medium|low"
}
```

**user**

```json
{"task":"daily_project_checkup","slot":"0900","company_id":1,
 "projects":[{"id":1,"name":"","goal":"","period":"","progress":62,"expected_progress":71,
   "overdue_tasks":[{"title":"短视频批量剪辑","owner":"周研","days":2}],
   "load":[{"name":"周研","pct":96}],
   "money":{"received":171600,"receivable":138400,"overdue":3},
   "devices":[{"name":"深圳机 1","online":true,"offline_minutes":0}],
   "work_logs":[...], "last_checkup":{...本次为 17:00 时带上今早 09:00 的结论...}}]}
```

### 14.4 落库与推送

    m_project_checkup(id, company_id, project_id, slot, checked_on, health, headline,
                      conclusion, evidence JSON, suggestions JSON, decisions JSON,
                      changes JSON, confidence, model, created_at)
      UNIQUE(company_id, project_id, slot, checked_on)     -- 幂等

    m_checkup_push(id, checkup_id, channel, target_user_id, status, sent_at, error)

- 渠道：站内简报流（必发）、微信 / 企微（可配）、需要时电话提醒（仅 risk 且老板开启）。
- 老板在简报里点开某条 → 看证据、建议、决策项，可直接「同意并执行」→ 生成任务/招聘/预算动作。

### 14.5 与工作安排、达成条件的联动

- 日检发现「建议」里涉及改人、改期 → 直接写入安排表的**待确认变更**（不静默改已确认的计划）。
- 日检发现新的资源缺口 → 追加到「达成条件」页，并标 `source=daily_checkup`。
- 连续 2 次指出同一责任人无响应 → 升级为 decisions（换人/加人）。
