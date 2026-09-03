//  App.swift
//  Entry point: a menu bar presence plus a main window.
//
//  The menu bar item is the point of the app - the assistant is *there*,
//  always, one click away, with its state visible in the icon.

import SwiftUI

@main
struct PersonalAIOSApp: App {
    @StateObject private var model = Assistant()

    var body: some Scene {
        Window("Personal AI OS", id: "main") {
            ContentView(model: model)
                .frame(minWidth: 760, minHeight: 520)
                .preferredColorScheme(.dark)
        }
        .defaultSize(width: 940, height: 660)
        .windowResizability(.contentMinSize)

        MenuBarExtra {
            MenuBarPanel(model: model)
        } label: {
            // The icon itself reports state: halted, waiting on you, or fine.
            Image(systemName: model.snapshot.stopped
                  ? "stop.circle.fill"
                  : (model.snapshot.waiting > 0 ? "hand.raised.circle.fill"
                                                : "circle.hexagongrid.circle"))
        }
        .menuBarExtraStyle(.window)
    }
}

struct MenuBarPanel: View {
    @ObservedObject var model: Assistant
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Core(busy: model.busy, stopped: model.snapshot.stopped)
                    .scaleEffect(0.42)
                    .frame(width: 56, height: 56)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Personal AI OS")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(Palette.text)
                    Text(model.snapshot.stopped ? "halted"
                         : model.busy ? model.busyLabel : "standing by")
                        .font(monoFont).foregroundColor(Palette.dim)
                }
                Spacer()
            }

            if model.snapshot.waiting > 0 {
                Text("\(model.snapshot.waiting) action\(model.snapshot.waiting == 1 ? "" : "s") waiting for you")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(Palette.amber)
            }

            Divider().background(Palette.line)

            VStack(spacing: 6) {
                QuickButton(title: "Open", symbol: "macwindow") {
                    openWindow(id: "main")
                    NSApplication.shared.activate(ignoringOtherApps: true)
                }
                QuickButton(title: "Allow a folder…", symbol: "folder.badge.plus") {
                    model.openFolderPicker(writable: false)
                }
                if model.snapshot.stopped {
                    QuickButton(title: "Resume", symbol: "play.fill", tint: Palette.green) {
                        model.resume()
                    }
                } else {
                    QuickButton(title: "Emergency stop", symbol: "stop.fill", tint: Palette.red) {
                        model.emergencyStop()
                    }
                }
                QuickButton(title: "Quit", symbol: "power", tint: Palette.dim) {
                    NSApplication.shared.terminate(nil)
                }
            }
        }
        .padding(14)
        .frame(width: 262)
        .background(Palette.void)
        .onAppear { model.refresh() }
    }
}
