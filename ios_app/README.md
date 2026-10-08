# 必火智能 iOS App 说明

## 先说明两个 iOS 工程

仓库里的 `ios_app/` 是之前从 `openmindapi/mobile` 迁入的 Capacitor H5 壳，目录结构如下：

- `ios_app/h5`：独立的 H5 副本
- `ios_app/ios/App`：Capacitor 生成的 Xcode 工程
- `ios_app/capacitor.config.ts`：App 名称、Bundle ID 和 H5 地址配置

它目前仍保留 OpenMind H5 的部分品牌和配置，属于源码迁移版，不能直接当作已经完成的“必火智能正式发布包”。正式发布前需要确认 App 名称、图标、Bundle ID、服务地址、登录、支付和苹果审核要求。

必火现有的原生 Swift 工程在本机另一个目录：

```text
/Users/jianji/Documents/Codex/2026-06-02/windows-mac/lobster_launcher/lobster_ios
```

这个工程使用 `WKWebView` 加载必火 H5，当前更接近必火 App 的实际工程。

## 打开必火原生工程

```bash
cd /Users/jianji/Documents/Codex/2026-06-02/windows-mac/lobster_launcher/lobster_ios
open -a "/Users/jianji/Downloads/Xcode-beta.app" LobsterIOS.xcodeproj
```

命令行构建使用本机 Xcode：

```bash
DEVELOPER_DIR="/Users/jianji/Downloads/Xcode-beta.app/Contents/Developer" \
xcodebuild -project LobsterIOS.xcodeproj -scheme LobsterIOS \
-sdk iphonesimulator -configuration Debug \
-derivedDataPath build CODE_SIGNING_ALLOWED=NO build
```

## 当前已有的安装包

目前没有生成可安装到真实 iPhone 或上传 App Store 的 `.ipa`。

本机目前找到的只有模拟器 App：

```text
/Users/jianji/Documents/Codex/2026-06-02/windows-mac/lobster_launcher/lobster_ios/build/Build/Products/Debug-iphonesimulator/LobsterIOS.app
```

这个 `.app` 只能用于 iOS Simulator，不能直接发给用户安装，也不能上传 App Store。

## 生成正式 IPA

正式包需要 Apple Developer 账号、签名证书、Provisioning Profile，以及 Xcode 中配置好的 Team 和 Bundle ID。

先归档：

```bash
cd /Users/jianji/Documents/Codex/2026-06-02/windows-mac/lobster_launcher/lobster_ios
./scripts/archive_release.sh
```

归档完成后导出：

```bash
./scripts/export_app_store.sh
```

成功后通常会在下面目录生成 IPA 或导出文件：

```text
/Users/jianji/Documents/Codex/2026-06-02/windows-mac/lobster_launcher/lobster_ios/build/export
```

也可以在 Xcode 中选择 `Product > Archive`，再通过 Organizer 上传 TestFlight 或 App Store Connect。

## Capacitor 壳的构建方式

如果后续确认要继续使用 `ios_app/` 这份 Capacitor 工程，先进入目录：

```bash
cd /Users/jianji/Documents/Codex/2026-06-02/windows-mac/lobster_launcher/lobster_server/ios_app
npm install
npm run h5:install
npm run h5:build
npm run cap:sync
npm run ios:open
```

正式构建前必须修改 `capacitor.config.ts` 中的 `appId` 和 `appName`，并设置真实 HTTPS 服务地址。当前这份壳包含网页充值、第三方 OAuth 和账号功能，不能不经调整就直接提交 App Store。
