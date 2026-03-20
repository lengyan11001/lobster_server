# lobster_server · 一键部署（开发机）

架构见 `docs/云服务器部署说明.md`、`docs/在线版架构-对话与OpenClaw.md`。

## 日常更新（一条命令）

在 **`lobster-server`** 仓库根目录（已 `git commit`）执行：

```bash
chmod +x scripts/deploy_server.sh scripts/deploy_from_local.sh
bash scripts/deploy_server.sh
```

1. `git push origin main`
2. 按 `.env.deploy` SSH 到服务器，在部署目录 `git pull` 并执行 `server_update_and_restart.sh`

## 首次准备（一次）

复制 `cp .env.deploy.example .env.deploy`，填写：

- `LOBSTER_DEPLOY_HOST` — 如 `root@47.120.39.220`
- `LOBSTER_DEPLOY_SSH_KEY` — 本机私钥路径
- `LOBSTER_DEPLOY_REMOTE_DIR` — 服务器上仓库路径，如 `/root/lobster_server`

服务器上需已 `git clone` 该仓库且能 `git pull`。

## 与 lobster_online 的关系

**不**把 `lobster_online` 部署到这台 ECS；用户本机解压代码包运行客户端。Server 只提供账号、鉴权、积分、速推网关、upload-temp 等。
