# manage.bhzn.top —— 项目管理与 AI 赋能系统 · 整体设计

> 目标：一个独立站点（manage.bhzn.top），把「组织 / 产品 / 项目 / 计划 / 执行 / 交付 / 回款」
> 串成一条链，让老板用 AI 出规划、让每个岗位的人看到自己该干的事并回填进度，
> 并把现有 Online/Server 的 AI 员工（设备槽位）当成虚拟员工接进来派活。
>
> 本文是落地依据：数据模型、接口、权限矩阵、页面清单、AI 规划契约、分期计划都写死了。

---

## 1. 定位与边界

**做什么**

- 组织：公司（一个老板可有多家）、员工、岗位（可多选）、每个岗位的级别、备注。
- 产品：可销售的东西及其基本详情（价格、周期、交付物、适用场景）。
- 项目：周期、目标、参与人、产品 -> AI 出规划 -> 老板补充要求 / 改后重新规划 -> 版本化。
- 执行：计划呈现（阶段 / 任务 / 责任人 / 里程碑）+ 进度跟踪 + 工作记录。
- 岗位工作台：业务管客户与进度、交付管已成交客户的交付、财务管回款、商务管渠道线索。
- 打通：把 Online 的设备槽位 / AI 员工登记为虚拟员工，安排任务，结果回流到项目进度。

**不做什么（避免和现有产品打架）**

- 不替代 Online 客户端：内容生产、发布、抖音/微信执行仍在客户端与现有 Server 上跑。
- 不在 manage 里重做一套账号体系：登录、注册、验证码、微信扫码全部复用现有认证中心。
- 不在 manage 里做计费：需要消耗积分的动作仍走现有 /mcp-gateway 与 pre-deduct。

**边界一句话**：manage 负责「人、货、项目、计划、进度、结果」，执行算力仍归 Online/Server。

---

## 2. 复用的现有底座（已核实，不重造）

| 能力 | 现有实现 | manage 怎么用 |
|---|---|---|
| 独立服务范本 | backend/app/h5_main.py（lobster-h5.service，8010） | 照抄成 backend/app/manage_main.py（8020，lobster-manage.service） |
| 登录 / JWT | backend/app/api/auth.py（router 直接被 h5 复用） | 直接 include_router(auth_router)，同一套账号密码、验证码、微信扫码 |
| 用户表 | models.User（id/email/role/brand_mark/...） | 只读复用；role=="admin" 是平台管理员 |
| 平台管理员 | api/admin.py：X-Admin-Token 或 JWT 且 role=="admin" | 「标记某人为老板」加在这里 |
| 品牌/主体 | models.BrandConfig + User.brand_mark | 与公司做弱关联（可选），公司主体仍以 m_company 为准 |
| 设备槽位 | UserInstallation / InstallationSlotOwner / MobileDeviceBinding | 虚拟员工 = 槽位，见第 8 节 |
| 定时任务 | api/scheduled_tasks.py（建任务 / run-now / runs / 事件 / 完成） | 给虚拟员工派活的通道，见第 8 节 |
| 客户 | models.Customer（owner_user_id 作用域）+ CustomerCommunication | 业务侧的客户主档，manage 加公司/项目维度，见 4.3 |
| H5 前端 | h5_static/ + backend/app/h5_main.py 静态挂载 | manage 前端独立目录 manage_static/ |
| 部署 | scripts/install_systemd_units.sh、scripts/deploy_from_local.py | 加一个 unit + 一个 nginx vhost |

---

## 3. 角色与权限

### 3.1 岗位（m_role_def.code，平台内置 + 公司可自定义）

用户说的"老板，业务，商务，财务等，你来补全"，补齐为下列 13 个：

| code | 中文 | 主要工作台 | 数据可见范围 |
|---|---|---|---|
| boss | 老板 / 负责人 | 全局看板、项目、规划、产品、组织、审批 | 本公司全部 |
| gm | 总经理 / 合伙人 | 全局看板、跨部门协调 | 本公司全部（可配置只看部分项目） |
| bd | 商务 | 渠道/合作方、线索池、报价 | 自己 + 公司线索池 |
| sales | 业务 | 客户增删改、跟进、商机阶段、成交 | 自己的客户（可授权协作） |
| delivery | 交付 | 已成交客户的交付单、验收 | 自己负责的交付单 |
| support | 客服 / 售后 | 工单、回访、续费提醒 | 自己负责的客户 |
| finance | 财务 | 合同、开票、回款、对账 | 本公司资金相关（金额可脱敏） |
| operation | 运营 | 内容/活动/投放节奏、素材需求 | 本公司运营数据 |
| content | 内容 / 设计 | 内容产出任务 | 自己的内容任务 |
| market | 市场 / 投放 | 投放计划、线索质量 | 本公司投放数据 |
| hr | 人事 | 员工档案、入职离职 | 员工档案（非工资） |
| supply | 采购 / 供应链 | 供应商、采购单 | 自己负责的采购 |
| admin | 平台管理员 | 建老板、平台配置 | 跨公司（仅平台级） |

> 岗位可多选：一个人 = 岗位集合 + 每个岗位各自的级别。

### 3.2 级别（每个岗位独立选）

intern 实习 -> p1 初级 -> p2 中级 -> p3 高级 -> p4 资深 -> lead 负责人

存法：m_membership_role(user_id, company_id, role_code, level, is_primary)，一人多行。

### 3.3 权限矩阵（节选，落地时以本文为准）

| 动作 | boss | gm | sales | delivery | finance | 普通成员 | admin |
|---|---|---|---|---|---|---|---|
| 建/改公司、加人、发邀请 | 可 | 可配 | - | - | - | - | 可 |
| 定义产品 | 可 | 可配 | - | - | - | - | 可 |
| 建项目 / 触发 AI 规划 | 可 | 可配 | - | - | - | - | - |
| 补充要求 / 重规划 | 可 | 可配 | - | - | - | - | - |
| 客户增删改 | 可 | 可 | 可(自己) | - | - | - | - |
| 看已成交客户 / 建交付单 | 可 | 可 | 只看 | 可 | 只看 | - | - |
| 回款 / 开票 | 可 | 可 | - | - | 可 | - | - |
| 标记某人为老板 | - | - | - | - | - | - | 可 |
| 派活给虚拟员工 | 可 | 可配 | - | - | - | - | - |

实现方式：m_membership_role -> 计算 permissions 集合 -> FastAPI 依赖 require_perm("project.create", company_id)，
不用散落的 if role == ...。数据层统一在 company_id 上做租户隔离，越权返回 404 而不是 403（不泄露存在性）。

---

## 4. 数据模型（新增 m_ 前缀表，PostgreSQL）

### 4.1 组织

    m_company(id, name, short_name, owner_user_id, brand_mark, industry, scale,
              status, created_at, updated_at)          -- 一个 boss 可有多行

    m_membership(id, company_id, user_id, display_name, phone, email, avatar,
                 joined_at, status, remark, created_at, updated_at)
      UNIQUE(company_id, user_id)

    m_membership_role(id, membership_id, company_id, role_code, level, is_primary)
      UNIQUE(membership_id, role_code)                 -- 岗位多选 + 每岗级别

    m_invite(id, company_id, code, role_code, level, expires_at, used_by_user_id, created_by)

    m_audit_log(id, company_id, actor_user_id, action, target_type, target_id,
                before, after, ip, created_at)

### 4.2 产品

    m_product(id, company_id, name, sku, category, price, currency, unit,
              cycle_days, deliverable, description, target_customer,
              status, created_by, created_at, updated_at)

    m_product_sku_option(id, product_id, name, price_delta, note)   -- 可选规格/套餐

### 4.3 客户（复用现有 customers + 扩展）

现有 customers 已有 owner_user_id/name/company/phone/tags/status/notes/last_contact_at。
新增列（可空，不影响 Online）：

    ALTER TABLE customers ADD COLUMN company_id INTEGER;      -- 归属公司
    ALTER TABLE customers ADD COLUMN stage VARCHAR(32);       -- 商机阶段
    ALTER TABLE customers ADD COLUMN progress SMALLINT;       -- 0-100
    ALTER TABLE customers ADD COLUMN deal_amount NUMERIC(14,2);
    ALTER TABLE customers ADD COLUMN closed_at TIMESTAMP;
    ALTER TABLE customers ADD COLUMN source_project_id INTEGER;

进度与跟进（新表，不动 Online 语义）：

    m_customer_track(id, company_id, customer_id, project_id, stage, progress,
                     next_action, next_follow_at, owner_user_id, updated_at)

    m_customer_track_log(id, track_id, company_id, actor_user_id, from_stage, to_stage,
                         from_progress, to_progress, note, attachments, created_at)

### 4.4 项目与计划

    m_project(id, company_id, name, code, goal, success_criteria,
              start_at, end_at, status, owner_user_id, created_by, created_at, updated_at)

    m_project_member(id, project_id, company_id, membership_id, role_code, duty)

    m_project_product(id, project_id, product_id, qty, amount, note)

    -- AI 规划：一次项目可以有多个版本，改一次就是新版本，永远可回溯
    m_plan(id, project_id, company_id, version, status, source,      -- source: ai|manual|ai_revised
           input_snapshot, raw_output, summary, created_by, created_at)

    m_plan_node(id, plan_id, project_id, company_id, parent_id, path, depth,
                node_type,          -- phase | task | milestone | risk
                title, detail, owner_kind,   -- human | virtual_employee | unassigned
                owner_membership_id, owner_ai_employee_id,
                start_at, end_at, weight, kpi, deliverable, status, progress,
                order_index, created_at, updated_at)

    m_plan_revision(id, plan_id, company_id, requested_by, instruction,
                    scope, created_at, result_plan_id)

    m_task_log(id, plan_node_id, company_id, actor_user_id, actor_ai_employee_id,
               kind, content, progress, attachments, created_at)

### 4.5 交付与回款

    m_delivery(id, company_id, project_id, customer_id, product_id,
               owner_membership_id, status,   -- pending|doing|review|accepted|waived
               started_at, promised_at, delivered_at, accepted_at,
               acceptance_note, created_at, updated_at)

    m_delivery_step(id, delivery_id, name, status, due_at, done_at, note, order_index)

    m_invoice(id, company_id, customer_id, project_id, amount, kind,   -- invoice|receipt
              no, issued_at, status, attachments, created_by)

    m_payment(id, company_id, customer_id, project_id, invoice_id, amount,
              paid_at, method, note, created_by)

### 4.6 虚拟员工（Online 槽位）

    m_ai_employee(id, company_id, name, avatar, kind,        -- slot|skill_bot
                  installation_id, slot_owner_user_id, h5_workflow_ref,
                  capabilities, status, last_seen_at, created_by, created_at)

---

## 5. AI 规划：输入、输出、重规划

### 5.1 触发

老板在项目里填：周期（起止）+ 目标 + 参与人（各自岗位/级别）+ 产品 -> 点「生成规划」。

### 5.2 输入快照（存 m_plan.input_snapshot，保证可复现）

    {
      "project": {"name": "", "goal": "", "success_criteria": "", "start_at": "", "end_at": ""},
      "products": [{"name": "", "price": 0, "cycle_days": 0, "deliverable": ""}],
      "members": [{"membership_id": 1, "display_name": "",
                   "roles": [{"code": "sales", "level": "p3"}], "remark": ""}],
      "virtual_employees": [{"id": 1, "name": "", "capabilities": []}],
      "requirements": "老板补充的要求（重规划时追加）"
    }

### 5.3 输出契约（强制 JSON，服务端校验，不合格直接报错重试，不做静默兜底）

    {
      "summary": "一句话总览",
      "phases": [
        {"title": "阶段名", "start_at": "2026-09-15", "end_at": "2026-10-05",
         "goal": "阶段目标",
         "tasks": [
           {"title": "任务名", "detail": "做法",
            "owner": {"kind": "human", "role_code": "sales", "membership_id": 3},
            "start_at": "", "end_at": "", "weight": 3,
            "kpi": "可量化验收标准", "deliverable": "产出物", "depends_on": []}
         ]}
      ],
      "milestones": [{"title": "首次成交", "at": "2026-09-30", "criteria": ""}],
      "risks": [{"title": "", "impact": "", "mitigation": ""}],
      "kpis": [{"name": "成交额", "target": ">=30万", "measure": "回款"}]
    }

落库映射：phases -> m_plan_node(node_type=phase)、tasks -> node_type=task（parent_id 指向阶段）、
milestones/risks -> node_type=milestone/risk。owner 解析成 owner_membership_id 或 owner_ai_employee_id；
解析不到的人 -> owner_kind=unassigned，界面上高亮让老板指派。

### 5.4 补充要求 / 重规划（关键体验）

1. 老板在规划页任意位置加批注：「首周必须完成 3 场直播」「财务只参与结算」。
2. 点「重新规划」-> 写 m_plan_revision（含 instruction），带上上一版 JSON + 修改指令再问一次模型。
3. 产出新 m_plan（version+1，source=ai_revised），旧版永久保留。
4. 界面提供版本对比（新增/删除/改期/换责任人四类高亮）+ 一键回滚到某版。
5. 已经产生执行记录的任务，重规划时按业务键合并（title+owner），不重建，避免丢进度。

---

## 6. 呈现与跟踪

| 视图 | 内容 | 用途 |
|---|---|---|
| 项目总览 | 目标 / 周期倒计时 / 整体进度 / 里程碑 / 风险 | 老板一眼看 |
| 甘特图 | 阶段-任务时间轴，责任人色块，延期红色 | 排期与冲突 |
| 看板 | 待开始 / 进行中 / 待验收 / 已完成，按人筛选 | 每个人的活 |
| 我的任务 | 当前人所有任务 + 今日待办 + 逾期 | 员工落地 |
| 进度上报 | 员工填进度 %、写工作记录、传附件 | 跟踪数据来源 |
| 客户漏斗 | 按商机阶段分布 + 每人转化率 | 业务管理 |
| 交付看板 | 已成交 -> 交付单状态 | 交付管理 |
| 资金看板 | 合同额 / 已回款 / 逾期 | 财务 |
| AI 复盘 | 按周/月自动总结：完成率、卡点、建议 | AI 赋能闭环 |

进度口径：任务进度 = 人工上报（默认）；若任务挂在虚拟员工上，则用其关联 run 的状态自动回填（第 8 节）。

---

## 7. 各角色登录后看到什么

| 角色 | 首页 |
|---|---|
| 老板 | 项目总览矩阵（多公司可切换）+ 待我审批 + 风险预警 |
| 商务 | 我的渠道/线索 + 待跟进 |
| 业务 | 我的客户（新增/编辑/跟进）+ 商机阶段 + 今日待跟进 + 成交录入 |
| 交付 | 待交付 / 交付中 / 待验收 + 交付步骤清单 |
| 客服 | 我的客户回访 + 工单 |
| 财务 | 待开票 / 待回款 / 逾期 + 对账 |
| 运营/内容/市场 | 我的任务 + 产出物提交 |
| 人事 | 员工档案与入职离职 |
| 虚拟员工 | 无登录；在老板/项目的「执行」里作为责任人出现 |

---

## 8. 与 Online / Server 打通（虚拟员工 = 槽位 + 定时任务）

现有链路（已核实）：客户端轮询 /api/scheduled-tasks/pending 领活 -> 执行 -> /event 上报进度 ->
/complete 回传 result_payload（含刚上线的 targets_detail）。

接入三步

1. 登记：老板在「虚拟员工」页，从本公司已登录的槽位里挑（UserInstallation / InstallationSlotOwner），
   生成 m_ai_employee（installation_id + 能力标签）。槽位只在授权后才可选择。
2. 派活：m_plan_node 指派给虚拟员工后，点「下发」-> 服务端建一条定时任务
   （/api/scheduled-tasks/tasks + run-now），payload 带上 m_plan_node_id。
3. 回流：该 run 的 status/progress/result_payload.targets_detail 反写 m_plan_node.progress，
   执行明细直接显示在项目的任务详情里（复用刚上线的「节点执行情况」）。

映射表：m_ai_task_link(id, plan_node_id, company_id, scheduled_task_id, run_id, created_at)。

---

## 9. 页面清单（manage 前端）

    /login                      复用现有登录（同 JWT）
    /                           工作台（按角色）
    /org                        组织架构（员工 / 岗位 / 级别 / 备注）
    /org/invite                 邀请链接
    /products                   产品
    /projects                   项目列表
    /projects/:id               项目总览（甘特 / 看板 / 里程碑 / 风险）
    /projects/:id/plan          规划（版本对比 / 补充要求 / 重规划）
    /projects/:id/execution     执行跟踪（含虚拟员工执行明细）
    /customers                  客户（业务）
    /customers/:id              客户详情（跟进时间线 / 进度）
    /deliveries                 交付
    /finance                    财务
    /ai-employees               虚拟员工（槽位）
    /admin/bosses               平台：把某人标记为老板
    /admin/companies            平台：公司

---

## 10. 部署（manage.bhzn.top）

1. 代码：同仓新增 backend/app/manage_main.py + backend/app/api/manage/* + manage_static/。
2. 服务：install_systemd_units.sh 增加 lobster-manage.service（python3 -m backend.manage_run，
   127.0.0.1:8020，复用同一 .env / SECRET_KEY / PostgreSQL）。
3. nginx：新增 vhost manage.bhzn.top -> proxy_pass http://127.0.0.1:8020; 并签发证书。
4. DNS：manage.bhzn.top A 记录指向大陆机 42.194.209.150
   （本机 DNS 被代理劫持为 fake-ip，必须在服务器或 DNS 服务商侧确认/新增）。
5. 发布：沿用 python scripts/deploy_from_local.py（reset 到 origin/main + 重启 unit）。

---

## 11. 分期落地计划

| 阶段 | 交付 | 验收 |
|---|---|---|
| P0 骨架 | manage_main + 登录复用 + systemd + nginx + 空壳首页 | 能打开 manage.bhzn.top 并用现有账号登录，看到「我的公司」空页 |
| P1 组织 | 公司/员工/岗位多选/级别/备注 + 邀请 + admin 标记老板 | 老板能建公司、加 3 个不同岗位的人、各自登录看到自己的档案 |
| P2 产品 | 产品 CRUD + 套餐规格 | 老板能录入产品并选进项目 |
| P3 项目 + AI 规划 | 项目 CRUD + 规划生成 + 版本 + 补充要求重规划 + 对比 | 同一条目标能出 2 版规划并对比/回滚 |
| P4 跟踪 | 甘特/看板/我的任务/工作记录/进度上报 | 员工上报后老板端进度实时变化 |
| P5 角色工作台 | 业务客户+漏斗、交付单、财务回款、客服/运营/人事 | 业务新增客户->成交->交付收到->财务回款，全链路可追 |
| P6 虚拟员工打通 | 槽位登记 + 下发定时任务 + 结果回流 | 给虚拟员工派一条活，客户端执行，项目里看到执行明细 |
| P7 权限与审计 | 权限矩阵落地 + 审计日志 + 数据隔离测试 | 越权用例全绿（跨公司访问返回 404） |

依赖关系：P0 -> P1 -> P2/P3 可并行 -> P4 -> P5 -> P6；P6 依赖现有定时任务链路（已可用）。

---

## 12. 待确认（不阻塞 P0/P1，先按本文假设推进）

1. manage.bhzn.top 的 DNS 与证书是否已就绪（本机无法判定，需在服务器确认）。
2. 客户主档：复用 customers 加列（本文默认）还是 manage 独立建 m_customer。
3. 老板跨公司：默认「一个账号可属多家公司，登录后切换」（本文默认）。
4. 是否要给员工发「工资/提成」字段（涉及财务敏感，默认不做，只做业绩与回款）。

---

## 13. 落地条件核验（已在生产服务器实测，2026-09-13）

| 项 | 实测结果 | 结论 |
|---|---|---|
| DNS | 服务器侧 `getent hosts manage.bhzn.top` -> `42.194.209.150` | **记录已存在**，无需新增（本机 DNS 被代理劫持为 198.18.x.x fake-ip，不可信） |
| 端口 | 8000(backend) / 8010(h5, 127.0.0.1) / 443 占用；**8020 空闲** | 直接用 8020，无冲突 |
| nginx | sites-enabled: hikongai / hikongai-cn-http / lobster / lobster-remote-support；`server_name` 仅 bhzn.top、www、h5.bhzn.top | **需新增 manage vhost** |
| 证书 | certbot 已装；`bhzn.top` 证书 SAN = bhzn.top / h5.bhzn.top / www.bhzn.top，**不含 manage**；子域有独立证书先例（todesk.bhzn.top） | **需为 manage.bhzn.top 签独立证书** |
| systemd | 现有 7 个 lobster-* unit（backend/mcp/h5/background/mastra/remote-support/pg-stat-sampler） | 新增 `lobster-manage.service` |

**P0 具体动作（可直接执行）**
1. 代码：`backend/app/manage_main.py` + `backend/manage_run.py` + `backend/app/api/manage/*` + `manage_static/`。
2. unit：在 `scripts/install_systemd_units.sh` 增 `lobster-manage.service`（`$PY -m backend.manage_run`，127.0.0.1:8020，`EnvironmentFile=$ROOT/.env`）。
3. vhost：照抄 `lobster-remote-support`（todesk）那份骨架，`server_name manage.bhzn.top`，`proxy_pass http://127.0.0.1:8020`，并保留 80 端口的 `/.well-known/acme-challenge/`。
4. 证书：`sudo certbot --nginx -d manage.bhzn.top`，成功后把 443 段指向 `/etc/letsencrypt/live/manage.bhzn.top/`。
5. 发布：`python scripts/deploy_from_local.py`（已跑通）。

---

## 14. 财务（基础已纳入范围）

老板要的是「一眼知道钱的情况」，所以财务不做凭证级记账，做**经营视角**。

| 模块 | 字段 | 谁能看/改 |
|---|---|---|
| 收入 `m_income` | 日期、客户、项目、产品、金额、类型(合同/回款/其他)、状态、备注 | finance 改；boss/gm 看 |
| 开销 `m_expense` | 日期、类别(人力/投放/云服务/租金/其他)、金额、收款方、状态、备注 | finance 改；boss/gm 看 |
| 应收/逾期 | 由 `m_invoice` + `m_payment` 推导（已定义于 4.5） | finance 改；boss 看 |
| 现金余额 | 期初 + 累计收支 | 系统算 |

**老板视图**：本月收入 / 本月开销 / 净利润 / 回款率 / 应收待回 + 近 6 月收支对比 + 最近流水。
后续可加：按项目/产品/客户维度的毛利、费用预算与超支预警、发票台账。

---

## 15. 老板简报系统（AI 生成 + 逐层下钻）

简报 = AI 每天/每周把「钱、项目、人、客户、风险」压成**一张能点开的卡**。

卡片结构：`类别 · 一句话结论 · 3 个关键数字 · 迷你趋势线 · 「详情 ›」`

内置 6 类：**经营简报 / 风险预警 / 项目简报 / 资金简报 / 团队简报 / AI 复盘**。

**下钻层级（点进详情抽屉）**：结论 → 关键数字 → 构成/对比（带进度条） → 明细表 → 单条记录 → 关联的人/项目/客户。
每一层都可继续点，最终落到原始记录（工作记录、流水、客户跟进、任务）。

生成方式：复用服务端 LLM（现有 chat/mastra 通道），服务端聚合指标 → 提示词产出 JSON → 落 `m_briefing`
（字段：kind、title、lead、stats、metrics、facts、rows、period、generated_at），前端只渲染不计算。

---

## 16. UI/UX 设计语言：「星穹 / NEBULA」（完全新设计）

> 明确**不复用**现有 H5/桌面的样式体系，按 To C 产品标准重做。

**基调**：深空底 + 玻璃拟态 + 发光描边 + 极细网格 + 数据等宽字。

设计令牌：

    --bg #04060d / --bg2 #080e1c
    --glass rgba(255,255,255,.045) + blur(22px) + 1px rgba(255,255,255,.085)
    --cyan #22d3ee   --indigo #6366f1   --violet #a855f7
    --ok #34d399     --warn #fbbf24     --bad #fb7185
    --txt #e9effb    --muted #8b98ae    --dim #5a6577
    --ease cubic-bezier(.16,1,.3,1)     圆角 12 / 18 / 26

**动效规范**（统一曲线，时长分级）：

| 场景 | 时长 | 手法 |
|---|---|---|
| 悬停/聚焦 | 180–280ms | 发光描边 + 轻微上浮 |
| 入场（卡片/行） | 400–600ms | `opacity + translateY`，列表**逐条 50–80ms 错峰** |
| 数字 | 900ms | 三次缓动 count-up，等宽字不跳动 |
| 图表 | 900–1250ms | 折线 `stroke-dashoffset` 生长；柱状 `scaleY`；进度条 `scaleX` |
| 页面切换 | 550ms | 内容上浮淡入，保留外壳不闪 |
| 抽屉 | 520ms | 右侧滑入 + 背景模糊遮罩 |

**组件库**：玻璃卡、发光主按钮（扫光）、KPI 卡（数字+迷你趋势）、简报卡（左侧色条+详情箭头）、
下钻抽屉（分层区块）、看板列、甘特条、脑图节点、任务行、标签、空态/骨架屏。

---

## 17. 登录页（To C 特效）

1. **背景**：星域粒子（canvas，近距自动连线）+ 三团极光缓慢漂移 + 底部网格渐隐。
2. **品牌**：渐变光斑徽标（呼吸 + 缓慢色相漂移），字距放大的 MANAGE。
3. **卡片**：入场 `scale(.97) → 1` + `blur(14px) → 0`，1s 弹性曲线。
4. **输入框**：聚焦时描边点亮 + 3px 外发光，标签变青。
5. **提交**：按钮内扫光循环 + 文案「正在验证身份…」→ **横向光带**扫过全屏 → 登录卡放大淡出 → 主界面淡入上浮。
6. 同一套账号体系（复用现有认证中心），所以用户不需要新密码。

---

## 18. 老板创建任务的分析过程（重点交互）

不是"转圈等结果"，而是**让规划长出来**：

1. **思考期**：中心球体呼吸 + 光环扩散，左下角逐行打出分析日志
   （解析目标 → 拉取成员岗位 → 匹配产品 → 参考历史项目 → 拆解阶段 → 分派责任人 → 生成 KPI）。
2. **脑图生长**：根节点「目标」→ 阶段节点逐个落位 → 每个阶段下的任务节点依次长出，
   连线用 SVG `stroke-dashoffset` **描边生长**，节点弹性缩放落位。
3. **表格刷出**：右侧任务清单与脑图**同步**逐行滑入（错峰 105ms），状态用颜色区分（完成/进行中/逾期/新增）。
4. **任意节点可批注**：点节点 → 气泡输入「首周必须完成 3 场直播」，节点右上角出现批注角标。
5. **重新规划**：节点向中心收敛淡出 → 加载态 → **重新生长为 v2**；
   顶部版本条出现 `v2 · 已重规划 · 批注 1`，表格里新增任务标「新增」。
6. **多视图**：脑图 / 表格 / 甘特 / 看板可切换（morph 过渡），老板按习惯看。

---

## 19. To C 质量标准（验收线）

- 首屏可交互 < 1.5s；切换视图无白屏；所有异步都有骨架屏。
- 动效 60fps（只动 `transform/opacity`，不触发布局）。
- 空态 / 加载 / 错误 / 无权限四种状态都要有画面，不能只弹 toast。
- 深色为主，对比度 ≥ 4.5:1；触达区域 ≥ 40px。
- 键盘可用（Enter 提交、Esc 关抽屉）、抽屉可关闭、焦点不丢失。
- 文案要有"人味"：结论先行、数字带单位和对比、失败给下一步建议。

---

## 20. 原型与截图

- 可运行原型：`docs/manage-prototype/index.html`（单文件，无外部依赖，双击即开；
  含登录特效 / 简报下钻 / AI 规划生长 / 批注重规划 / 财务 / 组织 / 虚拟员工）。
- 设计确认后，原型拆成 `manage_static/`（CSS 令牌 + 组件 + 视图），随 P0 一起上线。
