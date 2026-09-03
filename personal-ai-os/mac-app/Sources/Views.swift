//  Views.swift
//  The interface.
//
//  Visual language: deep space ground, one cyan accent that glows, thin
//  monospace labels, and a central "core" that breathes slowly and spins up
//  when the assistant is working. Everything else stays quiet so the core and
//  the conversation carry the screen.

import SwiftUI

// MARK: - palette

enum Palette {
    static let void = Color(red: 0.031, green: 0.043, blue: 0.063)
    static let deep = Color(red: 0.055, green: 0.075, blue: 0.106)
    static let panel = Color(red: 0.078, green: 0.102, blue: 0.141)
    static let line = Color(red: 0.145, green: 0.192, blue: 0.251)
    static let text = Color(red: 0.878, green: 0.925, blue: 0.965)
    static let dim = Color(red: 0.482, green: 0.561, blue: 0.639)
    static let cyan = Color(red: 0.302, green: 0.847, blue: 0.910)
    static let cyanDeep = Color(red: 0.118, green: 0.478, blue: 0.596)
    static let amber = Color(red: 0.960, green: 0.686, blue: 0.310)
    static let red = Color(red: 0.937, green: 0.365, blue: 0.404)
    static let green = Color(red: 0.400, green: 0.847, blue: 0.588)
}

let monoFont = Font.system(size: 11, weight: .medium, design: .monospaced)

// MARK: - the core

/// The breathing ring at the top of the sidebar. Idle: slow pulse. Busy: the
/// outer ring spins, so "it is working" is visible from across the room.
struct Core: View {
    var busy: Bool
    var stopped: Bool

    @State private var spin = false
    @State private var breathe = false

    private var tint: Color { stopped ? Palette.red : Palette.cyan }

    var body: some View {
        ZStack {
            Circle()
                .fill(RadialGradient(colors: [tint.opacity(0.28), .clear],
                                     center: .center, startRadius: 2, endRadius: 62))
                .frame(width: 124, height: 124)
                .scaleEffect(breathe ? 1.06 : 0.94)

            Circle()
                .stroke(tint.opacity(0.18), lineWidth: 1)
                .frame(width: 96, height: 96)

            Circle()
                .trim(from: 0.0, to: 0.28)
                .stroke(tint, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: 96, height: 96)
                .rotationEffect(.degrees(spin ? 360 : 0))
                .opacity(busy ? 1 : 0.35)

            Circle()
                .trim(from: 0.55, to: 0.72)
                .stroke(tint.opacity(0.6), style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                .frame(width: 72, height: 72)
                .rotationEffect(.degrees(spin ? -360 : 0))
                .opacity(busy ? 1 : 0.25)

            Circle()
                .fill(tint)
                .frame(width: 9, height: 9)
                .shadow(color: tint.opacity(0.9), radius: busy ? 10 : 5)
                .scaleEffect(breathe ? 1.25 : 0.85)
        }
        .frame(height: 130)
        .onAppear {
            withAnimation(.linear(duration: 5.5).repeatForever(autoreverses: false)) {
                spin = true
            }
            withAnimation(.easeInOut(duration: 2.6).repeatForever(autoreverses: true)) {
                breathe = true
            }
        }
        .accessibilityLabel(stopped ? "Stopped" : (busy ? "Working" : "Idle"))
    }
}

// MARK: - small parts

struct Readout: View {
    let label: String
    let value: String
    var tint: Color = Palette.text

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label.uppercased())
                .font(monoFont)
                .foregroundColor(Palette.dim)
                .tracking(0.8)
            Spacer(minLength: 8)
            Text(value)
                .font(monoFont)
                .foregroundColor(tint)
                .multilineTextAlignment(.trailing)
                .lineLimit(2)
        }
    }
}

struct QuickButton: View {
    let title: String
    let symbol: String
    var tint: Color = Palette.cyan
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Image(systemName: symbol).font(.system(size: 11, weight: .semibold))
                Text(title).font(.system(size: 12, weight: .medium))
                Spacer(minLength: 0)
            }
            .foregroundColor(hovering ? tint : Palette.text.opacity(0.86))
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .fill(hovering ? tint.opacity(0.12) : Color.white.opacity(0.035))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 7)
                    .stroke(hovering ? tint.opacity(0.45) : Palette.line, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

// MARK: - conversation

struct MessageRow: View {
    let message: Message

    private var accent: Color {
        switch message.kind {
        case .you: return Palette.dim
        case .assistant: return Palette.cyan
        case .system: return Palette.cyanDeep
        case .failure: return Palette.red
        }
    }

    private var speaker: String {
        switch message.kind {
        case .you: return "you"
        case .assistant: return "assistant"
        case .system: return "system"
        case .failure: return "problem"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Rectangle()
                .fill(accent.opacity(message.kind == .you ? 0.25 : 0.85))
                .frame(width: 2)
                .cornerRadius(1)

            VStack(alignment: .leading, spacing: 5) {
                Text(speaker.uppercased())
                    .font(monoFont).tracking(1.0)
                    .foregroundColor(accent.opacity(0.9))

                Text(message.text)
                    .font(.system(size: 13.5))
                    .foregroundColor(message.kind == .you ? Palette.dim : Palette.text)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)

                if !message.tools.isEmpty {
                    Text("used " + message.tools.joined(separator: " · "))
                        .font(monoFont)
                        .foregroundColor(Palette.dim.opacity(0.75))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 7)
    }
}

struct ApprovalCard: View {
    let action: PendingAction
    let onDecision: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 7) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 11, weight: .bold))
                Text("PERMISSION NEEDED").font(monoFont).tracking(1.0)
            }
            .foregroundColor(Palette.amber)

            Text(action.summary)
                .font(.system(size: 13.5, weight: .medium))
                .foregroundColor(Palette.text)
                .fixedSize(horizontal: false, vertical: true)

            if !action.why.isEmpty {
                Text(action.why)
                    .font(.system(size: 12))
                    .foregroundColor(Palette.dim)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 9) {
                Button { onDecision(true) } label: {
                    Text("Allow").font(.system(size: 12, weight: .semibold))
                        .padding(.horizontal, 16).padding(.vertical, 6)
                        .background(RoundedRectangle(cornerRadius: 6).fill(Palette.amber))
                        .foregroundColor(Palette.void)
                }
                .buttonStyle(.plain)

                Button { onDecision(false) } label: {
                    Text("No").font(.system(size: 12, weight: .medium))
                        .padding(.horizontal, 16).padding(.vertical, 6)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.line, lineWidth: 1))
                        .foregroundColor(Palette.dim)
                }
                .buttonStyle(.plain)
                Spacer()
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 9).fill(Palette.amber.opacity(0.07)))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Palette.amber.opacity(0.35), lineWidth: 1))
        .padding(.vertical, 6)
    }
}
