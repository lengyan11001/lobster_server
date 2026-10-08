# 必火 AI 员工 · iOS 录音设备（AI 秘书 → 连接设备 → 同步音频）

安卓端这套功能是由 Android 宿主 App（`D:\lobster_h5_app`，包名 `com.bihuo.lobsterh5`）通过
`@JavascriptInterface` 把能力注入 H5 实现的，所以 iOS 上一直显示「请在最新版安卓 APK 中使用录音设备功能」。

这里给出 **iOS 侧的完整实现**，API 与事件协议和安卓 1:1 对齐；H5 侧本次也一起改了（见下），
所以 iOS 工程只要把这三个 Swift 文件接进去、加上蓝牙权限，就能跑同一套 H5。

## 目录

| 文件 | 作用 |
|---|---|
| `LobsterRecorderBridge.swift` | WKWebView 桥：注入 `window.LobsterIOS`，方法名与安卓完全一致；回吐 `lobster-recorder` 事件 |
| `LobsterRecorderController.swift` | AIREC BLE 全流程（扫描/连接/文件列表/下载/录音/删除）+ 上传 `/api/h5/recorder/files` |
| `LobsterRecorderCapacitorViewController.swift` | 仅仓库里那份 Capacitor 壳需要：把桥挂到 Capacitor 的 WKWebView |
| `Info.plist.snippet.xml` | 蓝牙权限片段（必加） |

## H5 侧已同步改动（这次一起提交）

| 位置 | 改动 |
|---|---|
| `h5_static/h5-app.js` `recorderNative()` | 由「只认 `window.LobsterAndroid`」改为 `window.LobsterIOS || window.LobsterAndroid` |
| `h5_static/h5-app.js` 4 处文案 | 「请在最新版安卓 APK 中…」→「请在必火 App 中…」 |
| `h5_static/h5-i18n-generated.js` | 同步补了 3 条英文翻译键 |

> 也就是说：**iOS 端不做桥也能看到正确文案**；做了桥之后功能直接可用。

## 协议对照（不要改名）

**JS 方法（`window.LobsterIOS`，与安卓 `MainActivity.java:229-271` 同名）**

```
startRecorderScan()
getRecorderState()            -> JSON 字符串（同步返回，桥内部用缓存状态）
setRecorderAuth(token, installationId, brand)
fetchRecorderFiles()
syncNewRecorderFiles(knownNamesJson)
downloadRecorderFile(fileName)
startRecorderRecording()
stopRecorderRecording(refreshAfterStop)
deleteRecorderFile(fileName)
```

**原生 → H5 事件**（`window.dispatchEvent(new CustomEvent('lobster-recorder', { detail: { type, ... } }))`）

```
scanStarted / deviceFound / connected / disconnected / fileList
recordState / recordDuration / syncStarted / syncProgress(下载进度用 downloadProgress)
downloadProgress / downloadComplete / downloadFailed / syncBlocked / syncInterrupted / syncComplete
fileDeleted / uploadComplete / uploadFailed
```

**上传**

```
POST {baseURL}/api/h5/recorder/files?brand=<brand>
Authorization: Bearer <token>
X-Lobster-Brand: <brand>
X-Installation-Id: <installationId>
multipart/form-data:
  device_name=<设备名>
  installation_id=<installationId>
  file=<录音文件>          Content-Type: audio/ogg
```

## 集成步骤（Mac / Xcode）

1. 把 `LobsterRecorderBridge.swift`、`LobsterRecorderController.swift` 加入 App target。
2. 引入录音笔厂商 SDK：`AIRECBleKit.framework`（Embed & Sign），与 `AIRECIOSBleDemo` 里那份一致。
3. `Info.plist` 按 `Info.plist.snippet.xml` 加蓝牙权限。
4. 创建 WKWebView 之后调用一次：

```swift
LobsterRecorderBridge.shared.baseURL = "https://h5.bhzn.top"   // 与 H5 站点一致
LobsterRecorderBridge.shared.attach(webView: webView)
```

   仓库里的 Capacitor 壳（`ios_app/`）直接用 `LobsterViewController`（见第 3 个文件）。
5. 可选：把 demo 里的 `ATWOpusConverter.swift`（ATW/KA → OGG Opus）一起放进 target，然后：

```swift
LobsterRecorderBridge.shared.convertForUpload = { path in ATWOpusConverter.convertToOgg(path) }
```
   不接也能跑：设备下载出来的文件会按原样以 `audio/ogg` 上传（与安卓当前行为一致）。

## 必须在真机验证的点（Windows 上无法编译）

- AIRECBleKit 的代理回调签名（`AIRECBleDelegate`）与 SDK 实际版本是否一致（以 demo 为准）
- 下载完成后的文件路径/格式（是否需要 ATW → OGG 转换，以及转换后时长是否正确）
- `AIRECBleManager.setup()` 需要在 App 生命周期内只调用一次（本实现放在 `attach(webView:)`）
- 上传成功与否以服务端返回 2xx 为准；失败会在 H5 上以 `uploadFailed` 弹出
- 后台/切前后台：安卓有 `syncInterrupted`，iOS 侧同理需要在 `didDisconnect` 后中断队列

## 联调自检

1. 打开 H5 的「AI 秘书」→「连接设备」，应弹出扫描列表（事件 `scanStarted` + `deviceFound`）
2. 连接成功后状态变「已连接」，下拉「同步」应看到 `fileList`
3. 选一条录音同步：`downloadProgress` → `downloadComplete` → `uploadComplete`，随后 AI 秘书出现转写记录
4. 服务器侧对应接口：`POST /api/h5/recorder/files`、`GET /api/h5/recorder/files`（见 `backend/app/api/h5_recorder.py`）