# 行为对齐说明：为什么是改 Server 而不是改单机

## 约定

- **单机版**：`lobster/` 目录，本地/standalone 运行，是**行为与产品约定的基准**。
- **在线版后端**：`lobster-server/`，部署在云上，对外提供在线版全部 API（对话、计费、技能等）。

当「在线版行为要和单机版一致」时，**只改 lobster-server，不改 lobster 单机**。

## 为什么是 Server 的改动？

1. **单机版是基准**  
   单机版的 prompt 规则、发布约束、素材指代、图生视频注入方式等已经按产品约定打磨好（用户文案原样进 prompt、图由系统注入、成功时按 `saved_assets[0].media_type` 说「图片已生成」/「视频已生成」等）。这些约定以单机实现为准。

2. **在线版复用同一套行为**  
   在线版用户通过浏览器连的是 lobster-server，对话、生成、发布等逻辑都由 server 完成。要让在线体验与单机一致，就需要在 **server 侧**补齐与单机相同的系统提示、用户附图文案和约束（理解视频/图片、发布约束、素材指代、「生成好了吗」不重复查、区分提交/查询、按 media_type 表述等），而不是去改单机代码。

3. **单机版保持稳定、独立迭代**  
   lobster 单机可能单独发版、在无网或内网环境使用，不应为「和在线对齐」而被动改逻辑。对齐方向只能是：**以单机为基准，在 server 上补齐**。

4. **谁提供 API，谁承担对齐**  
   在线版的所有请求都打到 lobster_server（见 `lobster_online/docs/架构说明_server与本地职责.md`），因此行为一致性由 server 实现负责；改 server 即可让所有连到该 server 的前端（包括 lobster_online 页面）统一获得与单机一致的行为。

## 实际操作

- 对照单机版 `lobster/backend/app/api/chat.py` 中的系统提示、`_build_user_content_with_attachments` 等，在 **lobster-server** 的 `backend/app/api/chat.py` 中做同样或等价的补充与修改。
- **不修改** `lobster/` 下任何文件；如需同步到在线前端（lobster_online）的文案或交互，在 lobster_online 仓库中单独维护。
