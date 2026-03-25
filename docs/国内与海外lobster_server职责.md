# 国内与海外 lobster_server 职责

同仓库 `main` 可部署多台。**在线版前端**：

| 变量 | 典型地址 | 职责 |
|------|----------|------|
| `API_BASE` | 大陆 `47.120.39.220:8000` | 登录、积分、支付、鉴权、技能商店等 |
| `MESSENGER_API_BASE` / `TWILIO_API_BASE` | 海外 `lobster-server.icu:8000` | Messenger、Twilio；Webhook 须公网可达 Meta/Twilio |

本地执行：`git push` 后 `bash scripts/deploy_from_local.sh`。若 `.env.deploy` 含 `LOBSTER_DEPLOY_HOST_OVERSEAS`，会再拉取并重启海外机。

**海外 SSH**：须与大陆一样，把本机 `LOBSTER_DEPLOY_SSH_KEY` 对应**公钥**写入海外机 `root` 的 `~/.ssh/authorized_keys`；若海外只用另一把私钥，在 `.env.deploy` 设 `LOBSTER_DEPLOY_SSH_KEY_OVERSEAS`。

详见 `docs/Messenger配置说明.md`。
