# OpenMind iOS App

这是 OpenMind H5 的 Capacitor iOS 壳。它复用现有 Go 服务端和三套 H5，但会把主站前端复制到 `mobile/h5` 单独维护，不碰原始 `web/default`。

- `/`：主控制台
- `/workbench-app`：聊天、图片、视频和工作流
- `/canvas-app`：无限画布和素材编辑

## 前置条件

- macOS
- Xcode（不是只安装 Command Line Tools）
- Node.js 22 或更高版本
- 一个已经部署并启用 HTTPS 的 OpenMind 服务地址

## H5 副本

`mobile/h5` 是从 `web/default` 复制出来的独立前端目录。后续只改这里，原始 H5 保持不动。

## 初始化

```bash
cd mobile
npm install
npm run h5:install
OPENMIND_APP_URL=https://your-domain.example npm run ios:add
```

`ios:add` 只需要执行一次。之后如果已经存在 `mobile/ios`，不要重复执行。

## 打开 Xcode

```bash
cd mobile
npm run h5:build
OPENMIND_APP_URL=https://your-domain.example npm run cap:sync
npm run ios:open
```

在 Xcode 中：

1. 选择 `App` target 和你的 Apple Development Team。
2. 把 Bundle Identifier 改成自己的反向域名，例如 `com.example.openmind`。
3. 在 `Signing & Capabilities` 中确认自动签名。
4. 选择模拟器或已连接的 iPhone，点击 Run。

## 直接运行

```bash
cd mobile
npm run h5:build
OPENMIND_APP_URL=https://your-domain.example npm run ios:run
```

开发阶段可以使用 HTTP，但 iOS 真机和正式发布应使用 HTTPS。`OPENMIND_APP_URL` 不应包含 `/workbench-app` 或 `/canvas-app`，默认打开主控制台，其他入口由 H5 内部导航进入。

## 当前边界

这个版本是可运行的 H5 壳，不等于可以直接提交 App Store。项目当前包含网页充值/订阅、第三方 OAuth、文件上传下载和流式请求，正式上架前需要分别处理：

- 数字额度和订阅的 Apple In-App Purchase 方案
- Sign in with Apple（如果继续提供第三方登录）
- 账号删除、隐私政策、隐私清单和数据用途说明
- 外部支付、OAuth 回调、文件分享在 WKWebView 中的原生处理
- AI 生成内容的举报、过滤和审核流程

内部使用、企业签名或 TestFlight 验证可以先使用这个壳测试实际 H5 体验；面向 App Store 的版本建议再增加原生账户、任务、分享和购买流程。
