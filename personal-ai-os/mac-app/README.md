# The desktop app

A real macOS app — menu bar icon, window, folder picker, click-to-approve —
sitting on top of the same assistant your Terminal talks to.

```bash
cd mac-app
./build.sh --install --run
```

That's it. No Xcode needed: the Swift compiler comes with the Command Line
Tools you already have.

---

## What you get

**A menu bar presence.** An icon at the top right, always there. It changes
shape to report state: normal, a raised hand when something needs your
approval, a stop sign when the emergency brake is on.

**A window that reads at a glance.** A breathing core that spins when the
assistant is working. A status column: mode, folders, files indexed, memories,
whether a model is reachable. And the conversation.

**Click, don't type.** "Allow a folder…" opens the normal macOS folder picker
instead of making you type a path. When permission is needed, a card appears
inline with **Allow** and **No** buttons — no copying an id into another
command.

**The emergency stop is always one click away**, in the window and in the menu
bar.

---

## How it relates to the Terminal

The app is a *face* for the assistant, not a second copy of it. It runs the
same `assistant` command you'd type, so:

- permissions, memory, the file index and the audit log are **shared**
- `assistant stop` in Terminal stops the app too, and vice versa
- anything you do in the app shows up in `assistant logs`
- if the app breaks, the Terminal still works, and the other way round

You need `./install.sh` to have been run first. The app says so plainly if it
hasn't.

## Why it shells out instead of speaking the socket protocol

Two reasons, both deliberate:

1. **One implementation of the protocol.** A second one in Swift would drift
   from the Python one, and the bugs would be miserable.
2. **No shell, so no injection.** Arguments go through an argv array. A
   question containing quotes, semicolons or backticks is just text. Building a
   command string and handing it to a shell would reintroduce exactly the hole
   the whole project exists to avoid.

The one place a shell *is* used is reading your login environment once at
startup — an app launched from Finder has never sourced your `~/.zshrc`, so it
wouldn't otherwise see `ANTHROPIC_API_KEY`. The shell is asked to print
specific variables; it's never given your input.

---

## Making it start automatically

**System Settings → General → Login Items → +** and pick
`/Applications/Personal AI OS.app`.

## Rebuilding after changes

```bash
./build.sh --install
```

The ad-hoc signature keeps the bundle identity stable, so macOS doesn't
re-ask for permissions every rebuild.

## If it won't build

The compiler prints the file and line. Copy everything from the first
`error:` line downwards — that's all that's needed to fix it.

Common ones:

| Message | Fix |
| --- | --- |
| `swiftc: command not found` | `xcode-select --install` |
| `needs macOS 13 or newer` | Use the Terminal version; the app needs Ventura+ |
| App opens and says "assistant isn't installed" | Run `./install.sh` in the parent folder first |
| No menu bar icon | Look at the far right; macOS hides icons when the bar is full |

## What it is not

Not a browser in a costume — it's a compiled AppKit/SwiftUI binary. Not a
second brain — it holds no state of its own; everything lives in the same
database the CLI uses. Not required — the assistant works fine without it.
