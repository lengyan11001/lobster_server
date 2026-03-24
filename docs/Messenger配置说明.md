# Messenger 多应用配置说明

## 一、架构要点

- **Webhook、Graph API** 仅部署在**可访问 Meta 的海外机**（如 `lobster-server.icu`）。
- **在线客户端**左侧 **「Messenger」** 仅将 CRUD 请求发到 **`MESSENGER_API_BASE`**（默认 `http://lobster-server.icu:8000`），与大陆登录 `API_BASE` 分离。
- **JWT**：浏览器携带的 `Authorization: Bearer` 必须在海外实例上可验证，且 **`users` 表能解析出同一用户**。常见做法：
  - **海外单独测**：直接在海外 lobster_server **注册账号**登录后配置；或
  - **生产**：大陆与海外 **共用 MySQL** + **相同 `SECRET_KEY`**（见 `.env`）。

## 二、配置项总表（按条「应用」填写）

在在线版 **Messenger** 页「添加应用」中填写，与 Meta 开发者后台一致：

| 字段 | 说明 |
|------|------|
| **显示名称** | 本地备注，便于区分多个 Facebook 应用。 |
| **Verify Token** | 自定义强随机字符串；在 Meta → 应用 → Messenger → Webhook 的 **Verify Token** 填**完全相同**的值。 |
| **App Secret** | Meta 应用面板 **应用密钥**；用于校验 Webhook POST 的 `X-Hub-Signature-256`。 |
| **Page ID** | Facebook **公共主页数字 ID**（与订阅的 Page 一致）；用于校验回调 `entry.id`。 |
| **Page Access Token** | 带 **pages_messaging** 等权限的 Page 令牌；用于调用 Graph 发消息。 |
| **产品知识（可选）** | 附加到 AI 系统提示，与企微「产品知识」类似。 |

保存后列表中会生成 **Webhook URL**，形如：

`{PUBLIC_BASE_URL}/api/messenger/callback/{callback_path}`

在 Meta 后台 **Callback URL** 填此完整地址（**HTTPS 生产**时请将 `PUBLIC_BASE_URL` 配为 `https://你的域名`，并保证 443 反代到后端）。

## 三、Meta 控制台操作顺序（每个应用）

1. 创建/选择 **Facebook 应用**，添加 **Messenger** 产品。
2. 关联 **Facebook 公共主页**，生成 **Page Access Token**（长期 Token 按 Meta 文档续期）。
3. **Webhook**：URL = 上节完整 URL；**Verify Token** = 本系统该条配置的 Verify Token；验证并保存。
4. **订阅字段**：至少勾选 **messages**。
5. 将 **App Secret**、**Page ID**、**Page Access Token** 填入本系统对应字段并保存。

## 四、服务端环境变量（仅海外机）

| 变量 | 说明 |
|------|------|
| `PUBLIC_BASE_URL` | 与对外访问一致，如 `http://lobster-server.icu:8000` 或 `https://lobster-server.icu`（配好 Nginx 后）。用于拼接返回前端的 `webhook_url`。 |
| `SECRET_KEY` | 与需验证 JWT 的环境一致（多机共用用户时与大陆相同）。 |

**不再**使用全局 `MESSENGER_PAGE_ACCESS_TOKEN` 作为业务主路径；多应用均以数据库 `messenger_configs` 为准。

## 五、验证步骤

1. `GET {MESSENGER_API_BASE}/docs` 可打开。
2. 登录后打开 **Messenger** 页，能 **列出/新增** 配置。
3. 在 Meta 后台点击 **验证 Webhook**，应成功。
4. 在 Messenger 窗口向主页发文本消息，海外日志应出现处理记录，并收到 AI 回复（需已配置对话模型或 OpenClaw，与企微通道一致）。
