# AI 客服自动回复梳理

## 目标

收到客户消息后，系统按渠道统一入库、去重、读取多轮历史和客服资料，再由“客服回复 Agent”生成结构化决策，最后由渠道适配器发送回复。

## 分层

1. 消息处理器
   - 负责收消息、去重、入库、取历史、重试、发送、记录状态。
   - 不直接写 prompt，不做通用聊天。

2. 客服回复 Agent
   - 只返回 `reply` / `handoff` / `ignore` / `failed`。
   - 不调用技能，不执行工具，不暴露内部规则。
   - 默认遇到人工、投诉、退款、合同、法律、隐私等场景转人工。

3. 渠道适配器
   - 企业微信：继续复用现有 pending/sync_msg + submit/send_msg 链路。
   - 微信协议助手：服务端保存实例和密钥，online 只展示设置、会话和重试入口。

## 已落地

- `backend/app/services/customer_service_agent.py`
  - 通用客服 Agent 决策层。

- `backend/app/models.py`
  - `JuheWechatConfig` 增加 AI 客服配置字段。
  - `auto_reply_memory_doc_ids` 保存选中的 OpenClaw 记忆文件 `doc_id`，客服资料优先从个人记忆/代理下发记忆读取。
  - 新增 `JuheWechatAiMessage` 记录微信协议助手客服会话消息。

- `backend/app/api/juhe_wechat.py`
  - `GET/POST /api/juhe-wechat/ai-reply/config`
  - `GET /api/juhe-wechat/ai-reply/memory-docs`
  - `GET /api/juhe-wechat/ai-reply/sessions`
  - `GET /api/juhe-wechat/ai-reply/messages`
  - `POST /api/juhe-wechat/ai-reply/incoming`
  - `POST /api/juhe-wechat/ai-reply/messages/{message_id}/retry`

## 接入真实入站消息

如果聚合微信提供 webhook：

1. server 新增 webhook endpoint。
2. 校验来源。
3. 解析出 `config_id/guid`、客户 username、文本内容、provider_msg_id。
4. 调用与 `/api/juhe-wechat/ai-reply/incoming` 相同的内部处理逻辑。

如果只提供拉取接口：

1. 不要由 online 高频轮询。
2. server 侧按开启 AI 客服的实例低频拉取，失败指数退避。
3. 每条消息必须用 provider_msg_id 去重。
4. 拉取到消息后走同一个入站处理逻辑。

## 注意

- 自动回复默认关闭。
- 微信协议助手实例密钥仍只在 server 数据库，不下发 online。
- 客服资料不在微信助手里单独上传，online 只选择个人记忆或管理后台下发的记忆文件；补充话术只用于临时活动、特殊口径等短期规则。
- 当前先提供手动/测试入站接口和会话闭环；真实自动收消息需要确认上游稳定的 webhook 或消息拉取接口。
