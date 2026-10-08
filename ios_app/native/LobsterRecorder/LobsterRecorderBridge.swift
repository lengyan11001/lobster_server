//
//  LobsterRecorderBridge.swift
//  必火 AI 员工 · H5「AI 秘书 → 连接设备 → 同步音频」iOS 原生桥
//
//  作用：把 Android 端 MainActivity.java 的 @JavascriptInterface 方法，在 iOS WKWebView 里
//  以同名同参的 JS 对象 window.LobsterIOS 暴露出来（H5 侧 recorderNative() 已兼容）。
//
//  协议来源（1:1 对齐，勿随意改名）：
//    · 安卓方法清单  D:\lobster_h5_app\app\src\main\java\com\bihuo\lobsterh5\MainActivity.java:229-271
//    · 安卓事件/上传  ...\RecorderBleController.java（emit(...) 共 17 种 type；上传 /api/h5/recorder/files）
//    · H5 调用方      lobster_server/h5_static/h5-app.js:16664 recorderNative() 起
//
//  集成（WKWebView 工程，例如 lobster_ios）：
//    1) 把本目录 3 个 Swift 文件加入 App target
//    2) 引入 AIRECBleKit.framework（Embed & Sign）
//    3) Info.plist 加蓝牙权限（见 Info.plist.snippet.xml）
//    4) AppDelegate/启动处： AIRECBleManager 由 LobsterRecorderController 内部完成 setup
//    5) 创建 WKWebView 后调用： LobsterRecorderBridge.shared.attach(webView: webView)
//
//  ⚠️ 本文件在 Windows 上编写，尚未经过 Xcode 编译；请在 macOS 上首次编译时按报错微调（主要是 SDK 可选回调的签名）。
//

import Foundation
import WebKit

@objc public final class LobsterRecorderBridge: NSObject {

    @objc public static let shared = LobsterRecorderBridge()

    /// H5 站点地址（上传与鉴权都用它），默认线上地址
    @objc public var baseURL: String = "https://h5.bhzn.top"
    /// 品牌标识，与 H5 的 brand 保持一致
    @objc public var brand: String = "bihuo"

    /// 上传前可选转换（例如接 demo 的 ATWOpusConverter：ATW/KA -> OGG Opus）
    @objc public var convertForUpload: ((String) -> String?)? {
        didSet { controller.convertForUpload = convertForUpload }
    }

    private static let handlerName = "lobsterRecorder"
    private weak var webView: WKWebView?
    private let controller = LobsterRecorderController()
    private var attached = false

    private override init() {
        super.init()
        controller.baseURL = baseURL
        controller.onEvent = { [weak self] json in
            self?.evaluate("window.LobsterIOS && window.LobsterIOS.__emit(\(json));")
        }
        controller.onStateChanged = { [weak self] json in
            self?.evaluate("window.LobsterIOS && window.LobsterIOS.__setState(\(json));")
        }
    }

    /// 在 WKWebView 创建之后调用一次
    @objc public func attach(webView: WKWebView) {
        guard !attached else { return }
        attached = true
        self.webView = webView
        controller.baseURL = baseURL
        controller.convertForUpload = convertForUpload
        controller.attach(webView: webView)

        let ucc = webView.configuration.userContentController
        ucc.add(self, name: LobsterRecorderBridge.handlerName)
        ucc.addUserScript(WKUserScript(source: LobsterRecorderBridge.shimScript,
                                       injectionTime: .atDocumentStart,
                                       forMainFrameOnly: false))
    }

    @objc public func detach() {
        guard let webView = webView else { return }
        webView.configuration.userContentController.removeScriptMessageHandler(forName: LobsterRecorderBridge.handlerName)
        attached = false
    }

    /// 原生 → H5 事件（对齐 Android 的 window.dispatchEvent(new CustomEvent('lobster-recorder', {detail: ...}))）
    private func evaluate(_ js: String) {
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript(js, completionHandler: nil)
        }
    }

    /// 注入到页面的 JS 垫片：方法名与 Android 端完全一致，getRecorderState() 同步返回缓存状态
    private static let shimScript = """
    (function () {
      if (window.LobsterIOS && window.LobsterIOS.__isLobsterIOS) return;
      var cached = {};
      var post = function (cmd, payload) {
        try {
          window.webkit.messageHandlers.lobsterRecorder.postMessage(Object.assign({ cmd: cmd }, payload || {}));
        } catch (e) {}
      };
      var api = {
        __isLobsterIOS: true,
        __state: cached,
        getRecorderState: function () { return JSON.stringify(api.__state || {}); },
        startRecorderScan: function () { post('startRecorderScan'); },
        setRecorderAuth: function (token, installationId, brand) { post('setRecorderAuth', { token: token || '', installationId: installationId || '', brand: brand || '' }); },
        fetchRecorderFiles: function () { post('fetchRecorderFiles'); },
        syncNewRecorderFiles: function (knownNamesJson) { post('syncNewRecorderFiles', { knownNames: knownNamesJson || '[]' }); },
        downloadRecorderFile: function (fileName) { post('downloadRecorderFile', { fileName: fileName || '' }); },
        startRecorderRecording: function () { post('startRecorderRecording'); },
        stopRecorderRecording: function (refreshAfterStop) { post('stopRecorderRecording', { refresh: !!refreshAfterStop }); },
        deleteRecorderFile: function (fileName) { post('deleteRecorderFile', { fileName: fileName || '' }); },
        __setState: function (json) { try { api.__state = JSON.parse(json || '{}'); } catch (e) { api.__state = {}; } },
        __emit: function (json) {
          try {
            var detail = JSON.parse(json || '{}');
            window.dispatchEvent(new CustomEvent('lobster-recorder', { detail: detail }));
          } catch (e) {}
        }
      };
      window.LobsterIOS = api;
    })();
    """
}

extension LobsterRecorderBridge: WKScriptMessageHandler {

    public func userContentController(_ userContentController: WKUserContentController,
                                      didReceive message: WKScriptMessage) {
        guard message.name == LobsterRecorderBridge.handlerName,
              let body = message.body as? [String: Any],
              let cmd = body["cmd"] as? String else { return }

        switch cmd {
        case "startRecorderScan":
            controller.startScan()
        case "setRecorderAuth":
            controller.setAuth(token: body["token"] as? String ?? "",
                               installationId: body["installationId"] as? String ?? "",
                               brand: body["brand"] as? String ?? brand)
        case "fetchRecorderFiles":
            controller.refreshFiles()
        case "syncNewRecorderFiles":
            controller.syncNewFiles(knownNamesJson: body["knownNames"] as? String ?? "[]")
        case "downloadRecorderFile":
            controller.downloadFile(fileName: body["fileName"] as? String ?? "")
        case "startRecorderRecording":
            controller.startRecording()
        case "stopRecorderRecording":
            controller.stopRecording(refreshAfterStop: body["refresh"] as? Bool ?? true)
        case "deleteRecorderFile":
            controller.deleteFile(fileName: body["fileName"] as? String ?? "")
        default:
            break
        }
    }
}