//  Bridge.swift
//  Talking to the assistant from the app.
//
//  DESIGN NOTE, AND IT MATTERS
//  ---------------------------
//  The app does NOT reimplement the daemon's socket protocol. It runs the
//  `assistant` command with `--json` and parses the result. Two reasons:
//
//    1. One implementation of the protocol, not two that can drift apart.
//    2. Arguments are passed as an argv array with NO shell involved, so a
//       question containing quotes, semicolons or backticks is just text.
//       Building a command string and handing it to a shell would reintroduce
//       exactly the injection hole the whole project is built to avoid.
//
//  The one place a shell IS used is harvesting the login environment, because
//  an app launched from Finder never sees what your ~/.zshrc exported. We ask
//  the shell to print specific variables and read them back - the shell is
//  never given user input.

import Foundation

enum BridgeError: LocalizedError {
    case notInstalled
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .notInstalled:
            return """
            I can't find the `assistant` command.

            Install it first, then reopen this app:
                cd ~/personal-ai-os-repo/personal-ai-os
                ./install.sh
            """
        case .failed(let message):
            return message
        }
    }
}

final class Bridge {

    /// Where the CLI might live, most likely first.
    private static let searchPaths = [
        "\(NSHomeDirectory())/.local/bin/assistant",
        "/usr/local/bin/assistant",
        "/opt/homebrew/bin/assistant",
    ]

    private static var cachedBinary: String?
    private static var cachedEnvironment: [String: String]?

    static func binaryPath() -> String? {
        if let cached = cachedBinary { return cached }
        for candidate in searchPaths where FileManager.default.isExecutableFile(atPath: candidate) {
            cachedBinary = candidate
            return candidate
        }
        return nil
    }

    /// Pull PATH and any API keys out of a login shell, once.
    ///
    /// A GUI app launched from Finder starts with a bare environment - it has
    /// never sourced your ~/.zshrc, so `$ANTHROPIC_API_KEY` is simply absent
    /// and the assistant would report "no model configured" even though your
    /// terminal works fine. Asking the login shell to echo the values it has
    /// is the standard fix.
    private static func environment() -> [String: String] {
        if let cached = cachedEnvironment { return cached }

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["HOME"] = NSHomeDirectory()

        let wanted = ["PATH", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PAIOS_HOME"]
        // A fixed script with no user input in it.
        let script = wanted.map { "printf '%s=%s\\n' \($0) \"$\($0)\"" }.joined(separator: "; ")

        let shell = Process()
        shell.executableURL = URL(fileURLWithPath: "/bin/zsh")
        shell.arguments = ["-lc", script]
        let pipe = Pipe()
        shell.standardOutput = pipe
        shell.standardError = Pipe()

        do {
            try shell.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            shell.waitUntilExit()
            if let text = String(data: data, encoding: .utf8) {
                for line in text.split(separator: "\n") {
                    let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                    if parts.count == 2 {
                        let key = String(parts[0])
                        let value = String(parts[1])
                        if !value.isEmpty { env[key] = value }
                    }
                }
            }
        } catch {
            // No login shell available - the defaults above still work for
            // everything except a cloud API key.
        }

        cachedEnvironment = env
        return env
    }

    /// Run one command. `arguments` is an argv array; nothing is ever
    /// concatenated into a shell string.
    static func run(_ arguments: [String], timeout: TimeInterval = 300) throws -> Any {
        guard let binary = binaryPath() else { throw BridgeError.notInstalled }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: binary)
        task.arguments = ["--json"] + arguments
        task.environment = environment()

        let output = Pipe()
        let errors = Pipe()
        task.standardOutput = output
        task.standardError = errors
        task.standardInput = FileHandle.nullDevice

        do {
            try task.run()
        } catch {
            throw BridgeError.failed("Could not start the assistant: \(error.localizedDescription)")
        }

        // Enforce the timeout for real. Without this a wedged command would
        // hang the app forever with no way back except Force Quit.
        var finished = false
        let lock = NSLock()
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
            lock.lock(); let done = finished; lock.unlock()
            if !done && task.isRunning { task.terminate() }
        }

        // Read before waiting: a full pipe buffer would otherwise deadlock a
        // command that produces a lot of output, like `logs` or `index build`.
        let outData = output.fileHandleForReading.readDataToEndOfFile()
        let errData = errors.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        lock.lock(); finished = true; lock.unlock()

        let stdoutText = String(data: outData, encoding: .utf8) ?? ""
        let stderrText = String(data: errData, encoding: .utf8) ?? ""

        if let parsed = try? JSONSerialization.jsonObject(with: outData) {
            return parsed
        }

        // Not JSON. The CLI prints a human-readable explanation to stderr for
        // real errors, so pass that through rather than inventing a message.
        let message = stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !message.isEmpty { throw BridgeError.failed(message) }
        let fallback = stdoutText.trimmingCharacters(in: .whitespacesAndNewlines)
        throw BridgeError.failed(fallback.isEmpty
            ? "The assistant exited with code \(task.terminationStatus) and said nothing."
            : fallback)
    }

    static func dictionary(_ arguments: [String], timeout: TimeInterval = 300) throws -> [String: Any] {
        let value = try run(arguments, timeout: timeout)
        guard let dict = value as? [String: Any] else {
            throw BridgeError.failed("Unexpected reply shape from `assistant \(arguments.first ?? "")`.")
        }
        return dict
    }
}
