//
//  LobsterRecorderController.swift
//  必火 AI 员工 · 录音设备（AIREC BLE）iOS 实现
//
//  与 Android 端 RecorderBleController.java 行为对齐：
//    · 事件：通过 LobsterRecorderBridge 回吐 window.dispatchEvent(new CustomEvent('lobster-recorder',{detail:{type:...}}))
//      共 17 种 type：connected / disconnected / syncInterrupted / syncStarted / syncBlocked / syncComplete /
//                    fileDeleted / recordState / recordDuration / downloadProgress / downloadComplete /
//                    downloadFailed / uploadComplete / uploadFailed / scanStarted / deviceFound / fileList
//    · 上传：POST {baseURL}/api/h5/recorder/files?brand=<brand>
//            Header: Authorization: Bearer <token> / X-Lobster-Brand / X-Installation-Id
//            multipart: device_name / installation_id / file（filename, Content-Type: audio/ogg）
//    · getRecorderState() 返回：{"name","recording","paused","duration","fileCount","syncing","syncRemaining"}
//
//  ⚠️ 在 Windows 上编写，未经过 Xcode 编译验证；SDK 回调签名如与 AIRECBleKit 实际不一致，按编译器提示微调。
//

import Foundation
import WebKit

final class LobsterRecorderController: NSObject {

    var baseURL: String = "https://h5.bhzn.top"
    /// 原生 → H5 事件（参数是 JSON 字符串）
    var onEvent: ((String) -> Void)?
    /// 状态变化（参数是 JSON 字符串，H5 的 getRecorderState() 直接读它）
    var onStateChanged: ((String) -> Void)?
    /// 上传前可选转换（例如接 demo 的 ATWOpusConverter 把 ATW/KA 转 OGG Opus）；返回新路径，nil 表示用原路径
    var convertForUpload: ((String) -> String?)?

    private weak var webView: WKWebView?
    private let manager = AIRECBleManager.shared
    private var authToken = ""
    private var installationId = ""
    private var brand = "bihuo"
    private var files: [AIRECBleFile] = []
    private var syncQueue: [String] = []
    private var recording = false
    private var paused = false
    private var recordDuration: Int64 = 0
    private var uploadCount = 0

    // MARK: - 生命周期

    func attach(webView: WKWebView) {
        self.webView = webView
        manager.delegate = self
        manager.setup()
        pushState()
    }

    // MARK: - H5 方法实现（与 Android 同名）

    func setAuth(token: String, installationId: String, brand: String) {
        authToken = token
        self.installationId = installationId
        if !brand.isEmpty { self.brand = brand }
        pushState()
    }

    func startScan() {
        emit("scanStarted", "{}")
        manager.startScan()
    }

    func refreshFiles() {
        if recording {
            emit("syncBlocked", "{\"reason\":\"recording\"}")
            return
        }
        manager.fetchFileList()
    }

    /// knownNamesJson 形如 ["a.opus","b.opus"]，只同步设备上还没同步过的新文件
    func syncNewFiles(knownNamesJson: String) {
        if recording {
            emit("syncBlocked", "{\"reason\":\"recording\"}")
            return
        }
        let known = Set((try? JSONSerialization.jsonObject(with: Data(knownNamesJson.utf8))) as? [String] ?? [])
        syncQueue = files.filter { !known.contains($0.fileName) }.map { $0.fileName }
        emit("syncStarted", "{\"total\":\(syncQueue.count)}")
        downloadNextInQueue()
    }

    func downloadFile(fileName: String) {
        guard let file = files.first(where: { $0.fileName == fileName }) else {
            emit("downloadFailed", "{\"fileName\":\(json(fileName)),\"reason\":\"设备文件列表中找不到该录音\"}")
            return
        }
        syncQueue = [fileName]
        downloadNextInQueue(file)
    }

    func deleteFile(fileName: String) {
        if recording {
            emit("syncBlocked", "{\"reason\":\"recording\"}")
            return
        }
        manager.deleteFile(fileName)
    }

    func startRecording() {
        manager.startRecord()
    }

    func stopRecording(refreshAfterStop: Bool) {
        manager.endRecord()
        if refreshAfterStop {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                self?.manager.fetchFileList()
            }
        }
    }

    // MARK: - 下载 / 上传

    private func downloadNextInQueue(_ single: AIRECBleFile? = nil) {
        if let file = single {
            manager.downloadFile(file)
            return
        }
        guard let next = syncQueue.first else {
            emit("syncComplete", "{}")
            pushState()
            return
        }
        guard let file = files.first(where: { $0.fileName == next }) else {
            syncQueue.removeFirst()
            downloadNextInQueue()
            return
        }
        emit("downloadProgress", "{\"fileName\":\(json(file.fileName)),\"progress\":0}")
        manager.downloadFile(file)
    }

    private func handleDownloadComplete(file: AIRECBleFile, localPath: String) {
        emit("downloadProgress", "{\"fileName\":\(json(file.fileName)),\"progress\":100}")
        let uploadPath = convertForUpload?(localPath) ?? localPath
        uploadRecording(sourcePath: uploadPath, fileName: file.fileName) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.uploadCount += 1
                self.emit("uploadComplete", "{\"fileName\":\(json(file.fileName))}")
            case .failure(let error):
                self.emit("uploadFailed", "{\"fileName\":\(json(file.fileName)),\"reason\":\(json(error.localizedDescription))}")
            }
            if !self.syncQueue.isEmpty { self.syncQueue.removeFirst() }
            self.pushState()
            self.downloadNextInQueue()
        }
    }

    private enum UploadResult {
        case success
        case failure(Error)
    }

    private func uploadRecording(sourcePath: String, fileName: String, completion: @escaping (UploadResult) -> Void) {
        guard !authToken.isEmpty else {
            completion(.failure(NSError(domain: "LobsterRecorder", code: 401,
                                        userInfo: [NSLocalizedDescriptionKey: "登录状态缺失，请重新登录后上传"])))
            return
        }
        guard let url = URL(string: "\(baseURL)/api/h5/recorder/files?brand=\(brand)") else {
            completion(.failure(NSError(domain: "LobsterRecorder", code: 400,
                                        userInfo: [NSLocalizedDescriptionKey: "上传地址无效：\(baseURL)"])))
            return
        }
        guard let fileData = try? Data(contentsOf: URL(fileURLWithPath: sourcePath)) else {
            completion(.failure(NSError(domain: "LobsterRecorder", code: 404,
                                        userInfo: [NSLocalizedDescriptionKey: "录音文件不存在：\(sourcePath)"])))
            return
        }

        let boundary = "----LobsterRecorder\(Int(Date().timeIntervalSince1970 * 1000))"
        var body = Data()
        let deviceName = manager.getConnectedDevice()?.name ?? "AIREC"

        func appendField(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!)
            body.append("\(value)\r\n".data(using: .utf8)!)
        }
        appendField("device_name", deviceName)
        appendField("installation_id", installationId)

        let safeName = fileName.replacingOccurrences(of: "\"", with: "_")
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/ogg\r\n\r\n".data(using: .utf8)!)
        body.append(fileData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
        request.setValue(brand, forHTTPHeaderField: "X-Lobster-Brand")
        if !installationId.isEmpty { request.setValue(installationId, forHTTPHeaderField: "X-Installation-Id") }
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body

        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error = error { completion(.failure(error)); return }
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            if (200..<300).contains(code) {
                completion(.success)
            } else {
                let text = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
                completion(.failure(NSError(domain: "LobsterRecorder", code: code,
                                            userInfo: [NSLocalizedDescriptionKey: "服务器上传失败 HTTP \(code) \(text.prefix(200))"])))
            }
        }.resume()
    }

    // MARK: - 状态 / 事件

    private func stateJson() -> String {
        let name = manager.getConnectedDevice()?.name ?? ""
        let syncing = !syncQueue.isEmpty
        return "{\"name\":\(json(name)),\"recording\":\(recording),\"paused\":\(paused),"
            + "\"duration\":\(recordDuration),\"fileCount\":\(files.count),"
            + "\"syncing\":\(syncing),\"syncRemaining\":\(syncQueue.count)}"
    }

    private func pushState() {
        onStateChanged?(stateJson())
    }

    private func emit(_ type: String, _ jsonBody: String) {
        // jsonBody 形如 {"k":v}；合并成 {"type":"...","k":v} 交给 H5
        let payload = (jsonBody == "{}") ? "" : String(jsonBody.dropFirst().dropLast())
        let body = payload.isEmpty ? "{\"type\":\(json(type))}" : "{\"type\":\(json(type)),\(payload)}"
        onEvent?(body)
        pushState()
    }

    private func json(_ value: String) -> String {
        let escaped = value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "\r", with: "\\r")
        return "\"\(escaped)\""
    }
}

// MARK: - AIRECBleDelegate（与 AIRECIOSBleDemo / Android 事件一一对应）

extension LobsterRecorderController: AIRECBleDelegate {

    func bleManager(_ manager: AIRECBleManager, didDiscover device: AIRECBleDevice) {
        emit("deviceFound", "{\"name\":\(json(device.name)),\"address\":\(json(device.identifier)),\"rssi\":\(device.rssi)}")
    }

    func bleManager(_ manager: AIRECBleManager, didChangeBluetoothState enabled: Bool) {
        if !enabled { emit("disconnected", "{\"reason\":\"bluetooth_off\"}") }
    }

    func bleManager(_ manager: AIRECBleManager, didConnect device: AIRECBleDevice) {
        manager.stopScan()
        files = device.fileList
        emit("connected", "{\"name\":\(json(device.name)),\"recording\":\(device.isRecording),\"paused\":false,\"duration\":\(device.recordDuration),\"fileCount\":\(files.count)}")
        manager.fetchAllDeviceInfo()
    }

    func bleManager(_ manager: AIRECBleManager, didDisconnect device: AIRECBleDevice?, reason: String) {
        emit("disconnected", "{\"reason\":\(json(reason))}")
        if !syncQueue.isEmpty {
            syncQueue.removeAll()
            emit("syncInterrupted", "{\"reason\":\"disconnected\"}")
        }
    }

    func bleManager(_ manager: AIRECBleManager, didUpdateDeviceInfo device: AIRECBleDevice) {
        recording = device.isRecording
        recordDuration = device.recordDuration
        pushState()
    }

    func bleManager(_ manager: AIRECBleManager, didUpdateFileList files: [AIRECBleFile]) {
        self.files = files
        let sorted = files.sorted { ($0.createTime + $0.fileName) > ($1.createTime + $1.fileName) }
        let items = sorted.prefix(20).map {
            "{\"fileName\":\(json($0.fileName)),\"fileSize\":\($0.fileSize),\"createTime\":\(json($0.createTime)),\"durationSec\":\($0.durationSec)}"
        }
        emit("fileList", "{\"files\":[\(items.joined(separator: ","))]}")
    }

    func bleManager(_ manager: AIRECBleManager, didDeleteFile fileName: String, success: Bool) {
        if success { files.removeAll { $0.fileName == fileName } }
        emit("fileDeleted", "{\"fileName\":\(json(fileName)),\"success\":\(success)}")
    }

    func bleManager(_ manager: AIRECBleManager, didChangeRecordState recording: Bool, fileName: String) {
        self.recording = recording
        if !recording { recordDuration = 0 }
        emit("recordState", "{\"recording\":\(recording),\"paused\":\(paused),\"duration\":\(recordDuration),\"reason\":\(json(fileName))}")
    }

    func bleManagerDidPauseRecord(_ manager: AIRECBleManager) {
        paused = true
        emit("recordState", "{\"recording\":\(recording),\"paused\":true,\"duration\":\(recordDuration),\"reason\":\"paused\"}")
    }

    func bleManager(_ manager: AIRECBleManager, didQueryRecordStatus recording: Bool, paused: Bool, fileName: String) {
        self.recording = recording
        self.paused = paused
        pushState()
    }

    func bleManager(_ manager: AIRECBleManager, didUpdateRecordDuration durationSec: Int64) {
        recordDuration = durationSec
        emit("recordDuration", "{\"duration\":\(durationSec)}")
    }

    func bleManager(_ manager: AIRECBleManager, didReceiveFirmwareVersion version: String) {
        pushState()
    }

    func bleManager(_ manager: AIRECBleManager, downloadProgress file: AIRECBleFile, progress: Int) {
        emit("downloadProgress", "{\"fileName\":\(json(file.fileName)),\"progress\":\(max(0, min(100, progress)))}")
    }

    func bleManager(_ manager: AIRECBleManager, downloadComplete file: AIRECBleFile, localPath: String) {
        handleDownloadComplete(file: file, localPath: localPath)
    }

    func bleManager(_ manager: AIRECBleManager, downloadFailed file: AIRECBleFile, reason: String) {
        emit("downloadFailed", "{\"fileName\":\(json(file.fileName)),\"reason\":\(json(reason))}")
        if !syncQueue.isEmpty { syncQueue.removeFirst() }
        downloadNextInQueue()
    }
}