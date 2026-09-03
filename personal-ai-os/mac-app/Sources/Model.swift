//  Model.swift
//  The app's state, and every operation it can perform.
//
//  All work happens on a background queue and every published change is made
//  back on the main queue, because SwiftUI requires that.

import AppKit
import Foundation
import SwiftUI

struct Message: Identifiable, Equatable {
    enum Kind { case you, assistant, system, failure }
    let id = UUID()
    let kind: Kind
    let text: String
    let tools: [String]
    let at = Date()

    init(_ kind: Kind, _ text: String, tools: [String] = []) {
        self.kind = kind
        self.text = text
        self.tools = tools
    }
}

struct PendingAction: Identifiable, Equatable {
    let id: String
    let summary: String
    let why: String
    let risk: String
}

struct Snapshot: Equatable {
    var mode: String = "…"
    var autoApproves: String = ""
    var scopes: [String] = []
    var scopeModes: [String] = []
    var indexedFiles: Int = 0
    var memories: Int = 0
    var waiting: Int = 0
    var modelReady: Bool = false
    var modelDetail: String = ""
    var stopped: Bool = false
    var stopReason: String = ""
    var installed: Bool = true
}

/// Not marked @MainActor on purpose: every @Published write below already
/// happens inside DispatchQueue.main.async, and combining the attribute with
/// a plain GCD hop is what trips strict concurrency checking on newer
/// toolchains. One rule instead: nothing here writes state off the main queue.
final class Assistant: ObservableObject {

    @Published var messages: [Message] = []
    @Published var pending: [PendingAction] = []
    @Published var snapshot = Snapshot()
    @Published var busy = false
    @Published var busyLabel = "thinking"
    @Published var sessionID: String?

    private let queue = DispatchQueue(label: "com.personalaios.bridge", qos: .userInitiated)

    // MARK: - lifecycle

    func start() {
        if Bridge.binaryPath() == nil {
            snapshot.installed = false
            messages.append(Message(.failure, BridgeError.notInstalled.localizedDescription))
            return
        }
        messages.append(Message(.system, "Ready. Ask me anything, or tell me what to do."))
        refresh()
    }

    // MARK: - operations

    func refresh() {
        perform({ try Bridge.dictionary(["status"]) }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let data): self.apply(status: data)
            case .failure: break   // a failed refresh must not spam the transcript
            }
        }
    }

    func send(_ text: String, echo: Bool = true) {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !busy else { return }

        // After an approval we re-run the same question so it actually
        // happens - but showing it twice would look like a stutter.
        if echo { messages.append(Message(.you, question)) }
        busyLabel = "thinking"

        var arguments = ["ask", question]
        if let session = sessionID { arguments += ["--session", session] }

        perform({ try Bridge.dictionary(arguments) }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let data):
                self.sessionID = data["session_id"] as? String ?? self.sessionID
                let reply = (data["text"] as? String) ?? ""
                let tools = (data["tools_used"] as? [String]) ?? []
                if !reply.isEmpty {
                    self.messages.append(Message(.assistant, reply, tools: tools))
                }
                self.pending = ((data["pending_approvals"] as? [[String: Any]]) ?? []).map {
                    PendingAction(
                        id: ($0["approval_id"] as? String) ?? "",
                        summary: (($0["summary"] as? String) ?? "")
                            .replacingOccurrences(of: "Waiting for your approval: ", with: ""),
                        why: ($0["why"] as? String) ?? "",
                        risk: "")
                }
                self.refresh()
            case .failure(let error):
                self.messages.append(Message(.failure, error.localizedDescription))
            }
        }
    }

    func decide(_ action: PendingAction, allow: Bool) {
        busyLabel = allow ? "doing it" : "cancelling"
        pending.removeAll { $0.id == action.id }
        perform({ try Bridge.dictionary([allow ? "approve" : "deny", action.id]) }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success:
                self.messages.append(Message(.system, allow
                    ? "Approved — \(action.summary)"
                    : "Refused — \(action.summary)"))
                if allow, let last = self.lastQuestion() { self.send(last, echo: false) }
                self.refresh()
            case .failure(let error):
                self.messages.append(Message(.failure, error.localizedDescription))
            }
        }
    }

    func emergencyStop() {
        perform({ try Bridge.dictionary(["stop", "--reason", "stopped from the app"]) }) { [weak self] result in
            guard let self else { return }
            if case .success = result {
                self.messages.append(Message(.failure, "STOPPED. Everything has halted."))
            }
            self.refresh()
        }
    }

    func resume() {
        perform({ try Bridge.dictionary(["resume"]) }) { [weak self] _ in
            self?.messages.append(Message(.system, "Resumed."))
            self?.refresh()
        }
    }

    func grant(_ path: String, writable: Bool) {
        busyLabel = "granting access"
        var arguments = ["permissions", "grant", path]
        if writable { arguments += ["--mode", "readwrite"] }
        perform({ try Bridge.dictionary(arguments) }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let data):
                let mode = (data["mode"] as? String) ?? "read"
                let where_ = (data["path"] as? String) ?? path
                self.messages.append(Message(.system, "Granted \(mode) access to \(where_)"))
                self.buildIndex()
            case .failure(let error):
                self.messages.append(Message(.failure, error.localizedDescription))
            }
        }
    }

    func buildIndex() {
        busyLabel = "reading your files"
        perform({ try Bridge.dictionary(["index", "build"]) }) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let data):
                let added = (data["added"] as? Int) ?? 0
                let scanned = (data["scanned"] as? Int) ?? 0
                self.messages.append(Message(.system,
                    "Catalogued \(scanned) file\(scanned == 1 ? "" : "s") (\(added) new)."))
                self.refresh()
            case .failure(let error):
                self.messages.append(Message(.failure, error.localizedDescription))
            }
        }
    }

    func openFolderPicker(writable: Bool) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = writable ? "Allow changes here" : "Allow reading here"
        panel.message = writable
            ? "Pick a folder the assistant may read AND change."
            : "Pick a folder the assistant may read."
        if panel.runModal() == .OK, let url = panel.url {
            grant(url.path, writable: writable)
        }
    }

    // MARK: - helpers

    private func lastQuestion() -> String? {
        messages.last(where: { $0.kind == .you })?.text
    }

    private func apply(status data: [String: Any]) {
        var snap = Snapshot()
        snap.installed = true
        snap.mode = (data["autonomy_mode"] as? String) ?? "?"
        snap.autoApproves = (data["auto_approves_up_to"] as? String) ?? ""
        let scopes = (data["scopes"] as? [[String: Any]]) ?? []
        snap.scopes = scopes.compactMap { $0["path"] as? String }
        snap.scopeModes = scopes.compactMap { $0["mode"] as? String }
        snap.indexedFiles = ((data["index"] as? [String: Any])?["files"] as? Int) ?? 0
        snap.memories = ((data["memory"] as? [String: Any])?["total"] as? Int) ?? 0
        snap.waiting = (data["pending_approvals"] as? Int) ?? 0

        if let kill = data["kill_switch"] as? [String: Any] {
            snap.stopped = true
            snap.stopReason = (kill["reason"] as? String) ?? "stopped"
        }

        if let models = (data["models"] as? [String: Any])?["roles"] as? [String: Any],
           let reasoning = models["reasoning"] as? [String: Any] {
            snap.modelReady = (reasoning["available"] as? Bool) ?? false
            snap.modelDetail = snap.modelReady
                ? ((reasoning["model"] as? String) ?? "ready")
                : ((reasoning["reason"] as? String) ?? "no model")
        }
        snapshot = snap
    }

    /// Run `work` off the main thread, deliver the result on it.
    private func perform<T>(_ work: @escaping () throws -> T,
                            done: @escaping (Result<T, Error>) -> Void) {
        busy = true
        queue.async {
            let result: Result<T, Error>
            do { result = .success(try work()) } catch { result = .failure(error) }
            DispatchQueue.main.async {
                self.busy = false
                done(result)
            }
        }
    }
}
