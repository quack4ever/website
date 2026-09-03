//  ContentView.swift
//  The main window: a quiet status sidebar and the conversation.

import SwiftUI

struct ContentView: View {
    @ObservedObject var model: Assistant
    @State private var draft = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider().background(Palette.line)
            conversation
        }
        .background(
            ZStack {
                Palette.void
                RadialGradient(colors: [Palette.cyan.opacity(0.055), .clear],
                               center: .init(x: 0.12, y: 0.1),
                               startRadius: 10, endRadius: 620)
            }
            .ignoresSafeArea()
        )
        .onAppear {
            model.start()
            inputFocused = true
        }
    }

    // MARK: - sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 16) {
            Core(busy: model.busy, stopped: model.snapshot.stopped)
                .frame(maxWidth: .infinity)

            VStack(alignment: .leading, spacing: 2) {
                Text("PERSONAL AI OS")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .tracking(1.6)
                    .foregroundColor(Palette.text)
                Text(model.busy ? model.busyLabel
                                : (model.snapshot.stopped ? "halted" : "standing by"))
                    .font(monoFont)
                    .foregroundColor(model.snapshot.stopped ? Palette.red : Palette.dim)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Divider().background(Palette.line)

            VStack(spacing: 7) {
                Readout(label: "mode", value: model.snapshot.mode)
                Readout(label: "folders",
                        value: model.snapshot.scopes.isEmpty ? "none"
                                                             : "\(model.snapshot.scopes.count)",
                        tint: model.snapshot.scopes.isEmpty ? Palette.amber : Palette.text)
                Readout(label: "files", value: "\(model.snapshot.indexedFiles)")
                Readout(label: "memories", value: "\(model.snapshot.memories)")
                Readout(label: "model",
                        value: model.snapshot.modelReady ? "ready" : "none",
                        tint: model.snapshot.modelReady ? Palette.green : Palette.amber)
            }

            if !model.snapshot.scopes.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(model.snapshot.scopes.enumerated()), id: \.offset) { index, path in
                        HStack(spacing: 5) {
                            Image(systemName: "folder.fill")
                                .font(.system(size: 8))
                                .foregroundColor(Palette.cyanDeep)
                            Text((path as NSString).lastPathComponent)
                                .font(monoFont).foregroundColor(Palette.dim).lineLimit(1)
                            if index < model.snapshot.scopeModes.count,
                               model.snapshot.scopeModes[index] == "readwrite" {
                                Text("rw").font(.system(size: 8, design: .monospaced))
                                    .foregroundColor(Palette.amber)
                            }
                        }
                    }
                }
            }

            Divider().background(Palette.line)

            VStack(spacing: 7) {
                QuickButton(title: "Allow a folder…", symbol: "folder.badge.plus") {
                    model.openFolderPicker(writable: false)
                }
                QuickButton(title: "Allow + let it change", symbol: "folder.badge.gearshape",
                            tint: Palette.amber) {
                    model.openFolderPicker(writable: true)
                }
                QuickButton(title: "Re-read my files", symbol: "arrow.clockwise") {
                    model.buildIndex()
                }
            }

            Spacer(minLength: 0)

            if model.snapshot.stopped {
                QuickButton(title: "Resume", symbol: "play.fill", tint: Palette.green) {
                    model.resume()
                }
            } else {
                QuickButton(title: "EMERGENCY STOP", symbol: "stop.fill", tint: Palette.red) {
                    model.emergencyStop()
                }
            }
        }
        .padding(18)
        .frame(width: 244)
        .background(Palette.deep.opacity(0.55))
    }

    // MARK: - conversation

    private var conversation: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        if !model.snapshot.installed {
                            notInstalledPanel
                        } else if model.snapshot.scopes.isEmpty {
                            firstRunPanel
                        }

                        ForEach(model.messages) { message in
                            MessageRow(message: message).id(message.id)
                        }

                        ForEach(model.pending) { action in
                            ApprovalCard(action: action) { allow in
                                model.decide(action, allow: allow)
                            }
                        }

                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(.horizontal, 26)
                    .padding(.vertical, 20)
                }
                .onChange(of: model.messages.count) { _ in
                    withAnimation(.easeOut(duration: 0.25)) {
                        proxy.scrollTo("bottom", anchor: .bottom)
                    }
                }
                .onChange(of: model.pending.count) { _ in
                    withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                }
            }

            inputBar
        }
        .frame(minWidth: 460)
    }

    private var firstRunPanel: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("I can't see anything yet")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(Palette.text)
            Text("""
                 By design, I start with access to nothing at all. Pick a folder \
                 on the left and I'll read it — then you can ask me about what's \
                 in there.
                 """)
                .font(.system(size: 13))
                .foregroundColor(Palette.dim)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9).fill(Palette.cyan.opacity(0.06)))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Palette.cyan.opacity(0.25), lineWidth: 1))
        .padding(.bottom, 14)
    }

    private var notInstalledPanel: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("The `assistant` command isn't installed")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(Palette.red)
            Text("""
                 This app is a face for the assistant, not the assistant itself. \
                 Run ./install.sh in Terminal first, then reopen this window.
                 """)
                .font(.system(size: 13))
                .foregroundColor(Palette.dim)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9).fill(Palette.red.opacity(0.07)))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Palette.red.opacity(0.3), lineWidth: 1))
        .padding(.bottom, 14)
    }

    private var inputBar: some View {
        VStack(spacing: 0) {
            Divider().background(Palette.line)
            HStack(spacing: 11) {
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(Palette.cyan.opacity(model.busy ? 0.3 : 0.85))

                TextField("Ask me anything, or tell me what to do…", text: $draft)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .foregroundColor(Palette.text)
                    .focused($inputFocused)
                    .disabled(model.busy || !model.snapshot.installed)
                    .onSubmit(submit)

                Button(action: submit) {
                    Image(systemName: model.busy ? "hourglass" : "arrow.up.circle.fill")
                        .font(.system(size: 19))
                        .foregroundColor(canSend ? Palette.cyan : Palette.line)
                }
                .buttonStyle(.plain)
                .disabled(!canSend)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
        }
        .background(Palette.deep.opacity(0.75))
    }

    private var canSend: Bool {
        !model.busy && model.snapshot.installed
            && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func submit() {
        guard canSend else { return }
        let text = draft
        draft = ""
        model.send(text)
    }
}
