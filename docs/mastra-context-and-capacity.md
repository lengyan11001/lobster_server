# AI 调度会话上下文与容量设计

## 目标

服务器调度层负责理解用户目标、检索个人资料、选择云端能力，并把必须依赖桌面登录态的任务下发给 Online。它不替代现有执行架构，也不把所有技能、文件和历史记录一次性发送给 LLM。

隔离键：

- Mastra thread：`h5:{brand}:{user_id}:{session_id}`
- Mastra resource：`{brand}:{user_id}`
- H5 会话、消息、授权和附件均再次按归属用户校验
- 默认权限为 `confirm`；`full` 只改变是否需要本轮确认，不绕过后端用户权限和素材归属校验

## 上下文策略

每轮模型输入由四部分组成：

1. 固定系统规则。
2. 较早会话的持久摘要，最多 16,000 字符。
3. Mastra 最近 10 条原始消息。
4. 本轮请求、素材 URL 清单及本轮工具结果。

摘要和本轮授权状态通过 Mastra 的非持久运行上下文注入，不拼接进用户消息；持久消息只保存用户原始请求和模型回答，避免最近 10 条消息重复携带同一份摘要。

保护规则：

- `TokenLimiterProcessor` 在每个 agent step 前执行，默认输入上限 48,000 tokens。
- 输出上限为 4,096 tokens；摘要输出上限为 1,800 tokens。
- 历史工具调用只保留最近两个工具步骤，避免旧的长结果反复进入模型。
- MCP 工具通过 `ToolSearchProcessor` 按需搜索，每次最多激活 3 个，不把完整工具目录放入每轮 prompt。
- 个人记忆先返回目录；按关键词最多读取 3 份、合计 16,000 字符。单文档按 ID 读取也限制为 16,000 字符。
- 图片使用受控公网 URL 输入，不传 base64。文档、音频和视频由工具按素材 ID 处理，不把二进制送入 LLM。
- 一轮最多 8 个素材、总计 200 MB；不同媒体类型还有单文件限制。

## 增量压缩

会话保留最近 5 个完整问答轮次。更早的已完成轮次满足以下任一条件时触发压缩：

- 新增可压缩轮次达到 4 个；
- 新增历史文本达到 12,000 字符。

压缩使用当前用户 JWT、品牌和同一个 `openai/gpt-5.6-sol` 路由，不使用公共静态密钥。摘要保存到 `h5_chat_sessions`，同时记录压缩到哪个消息 ID。原始 H5 消息不因压缩而删除，仍可用于审计和重新生成摘要。

没有直接启用 Mastra Observational Memory。其默认摘要模型需要独立静态凭证，而本系统的调用鉴权、品牌隔离和计费都绑定当前用户 JWT。当前实现保留了 Observational Memory 的核心思路，但把摘要调用放在用户鉴权链路内。

## 能力调度

确认前可调用只读能力：

- 检索当前用户的能力目录。
- 列出和读取个人记忆。
- 读取 IP 人设资料。
- 查询 Online 任务状态。

确认后或完全授权时可执行：

- 通过 MCP 搜索并调用用户实际有权限的图片、视频、音频、内容、发布等云端能力。
- 把用户明确提供的文本保存为个人记忆。
- 按素材 ID 把已上传的文档、图片、音频或视频整理为个人记忆。
- 修改 IP 人设资料调查字段。
- 把微信、抖音、视频号、阿里询盘、桌面软件和本机文件相关任务下发给 Online。

写操作同时经过工具层授权和后端 `parent_message_id + approval_id + user_id` 校验。模型不能仅通过构造提示词绕过确认。

## 并发与流式压力

当前默认值：

- 全局同时运行 4 个调度请求。
- 同一用户同时运行 1 个，避免单个账号占满所有槽位。
- Mastra 内部等待队列最多 32 个；H5 单用户最多保留 8 个在途消息。
- 流式文本每 160 字符或 250 ms 合并写入一次数据库。
- H5 SSE 有事件时快速轮询，空闲后降至 1.5 秒一次，并发送 keep-alive。
- 浏览器断开只关闭 SSE；后台调度和已下发的 Online 子任务继续执行。

状态接口 `/api/mastra-chat/status` 返回：

- 当前运行和等待请求数。
- 最大并发和队列深度。
- 平均排队等待时间及拒绝数。
- 数据库 pending/processing 数和最老任务等待时间。
- 生效的上下文 token 上限和最近消息数。

## 存储保留

- H5 临时流事件默认保留 7 天，最终事件默认保留 90 天。
- 活跃会话的原始消息不自动删除。
- 用户已删除的会话默认 90 天后物理删除。
- 无消息空会话默认 30 天后清理。
- Mastra 消息默认保留 180 天，thread 默认 365 天，trace 默认 14 天。
- 清理均按批次执行，避免大事务、长锁和 WAL 突增。

PostgreSQL 删除后空间先由数据库复用，不等于立即归还操作系统。生产库仍需常规 autovacuum 和磁盘告警。

## 当前服务器容量判断

生产机约 15.6 GB RAM、12.3 GB 可用，磁盘约 151 GB 可用；Mastra 空闲约 220 MB，systemd 内存上限 1 GB。当前 4 并发主要等待上游网络，CPU 不是首要瓶颈。48K 上下文、3 工具激活和 4 并发下，1 GB Mastra 限额可作为保守起点。

扩容触发条件：

- `average_queue_wait_ms` 持续超过 10 秒。
- 最老 pending 持续超过 60 秒。
- Mastra RSS 持续超过 800 MB 或发生 OOM restart。
- PostgreSQL 连接池等待、SSE 查询或事件写入成为主要慢查询。
- 上游模型允许更高并发且余额、限流策略已确认。

达到触发条件后优先横向拆分调度 worker，并保持同一 thread 的顺序处理；不要只把单进程并发从 4 直接调高。

## 与 Codex 和 Mastra 的对应关系

可确认的共同原则是：保留最近原文、把较早历史压缩成高信号摘要、详细资料按需检索、工具按需加载、长任务与前端连接解耦。

Mastra 提供了 Memory、TokenLimiter、ToolSearch、retention 和 Observational Memory。本系统使用前四项，并用用户鉴权的增量摘要替代直接启用 Observational Memory。

Codex 在长任务中会进行上下文压缩并从摘要继续工作。其内部实现细节不是本系统设计依据；这里只复用可观察到的交互原则，不声称复制 Codex 的内部算法。

## 可调环境变量

- `LOBSTER_MASTRA_CONTEXT_TOKEN_LIMIT=48000`
- `LOBSTER_MASTRA_LAST_MESSAGES=10`
- `LOBSTER_MASTRA_MAX_CONCURRENCY=4`
- `LOBSTER_MASTRA_MAX_CONCURRENCY_PER_USER=1`
- `LOBSTER_MASTRA_MAX_QUEUE_DEPTH=32`
- `LOBSTER_MASTRA_MAX_ACTIVE_PER_USER=8`
- `LOBSTER_MASTRA_SUMMARY_KEEP_TURNS=5`
- `LOBSTER_MASTRA_SUMMARY_MIN_TURNS=4`
- `LOBSTER_MASTRA_SUMMARY_MIN_CHARS=12000`
- `LOBSTER_MASTRA_MEMORY_RETENTION_DAYS=180`
- `LOBSTER_MASTRA_THREAD_RETENTION_DAYS=365`
- `LOBSTER_MASTRA_TRACE_RETENTION_DAYS=14`
- `LOBSTER_H5_CHAT_TRANSIENT_EVENT_DAYS=7`
- `LOBSTER_H5_CHAT_EVENT_DAYS=90`
- `LOBSTER_H5_CHAT_ARCHIVED_SESSION_DAYS=90`
