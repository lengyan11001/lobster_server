//
//  LobsterRecorderCapacitorViewController.swift
//  给仓库里 ios_app（Capacitor 壳）用的接入口：把同一套桥挂到 Capacitor 的 WKWebView 上。
//
//  用法（二选一）：
//    A. 直接把 Main.storyboard 里的 ViewController 类改成 LobsterViewController；
//    B. 或者在 SceneDelegate / AppDelegate 拿到 CAPBridgeViewController 后调用
//       LobsterRecorderBridge.shared.attach(webView: bridgeVC.webView)
//
//  ⚠️ 未经过 Xcode 编译验证；CAPBridgeViewController 的 webView 属性在 Capacitor 8 上可用。
//

#if canImport(Capacitor)
import UIKit
import Capacitor
import WebKit

final class LobsterViewController: CAPBridgeViewController {

    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        if let webView = self.webView {
            LobsterRecorderBridge.shared.attach(webView: webView)
        }
    }
}
#endif