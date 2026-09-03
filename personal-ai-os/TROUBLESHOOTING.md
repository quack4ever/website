# TROUBLESHOOTING

**Always start here:**

```bash
assistant doctor
```

It checks eleven things and, for anything wrong, tells you what failed, why, and
the exact command that fixes it. Most of this page is the long form of what it
already tells you.

---

## "command not found: assistant"

The command is installed but your Mac does not know where to look.

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Check it landed: `ls ~/.local/bin/assistant`. If that file does not exist, the
install did not finish — re-run `./install.sh`.

---

## "It says it cannot read my files"

Almost always: you have not granted the folder.

```bash
assistant permissions            # see what is granted
assistant permissions grant ~/Documents
```

If the folder *is* granted, check three things:

1. **Is it read-only?** Writing needs `--mode readwrite`.
2. **Is it on the sealed list?** `~/.ssh`, keychains, browser data and similar
   can never be reached. That is deliberate.
3. **Is it reached through a symlink?** Links are followed to their real
   destination, which may be outside your scope.

`assistant logs` shows the exact decision and the reason.

---

## "It keeps asking permission for everything"

That is `assisted` mode, the default, working correctly. To let it work more
freely:

```bash
assistant config set autonomy.mode supervised
```

Now LOW and MEDIUM actions run quietly and only HIGH and CRITICAL ask. To let a
specific standing rule apply:

```bash
assistant permissions rule add --effect allow --capability files.move \
    --path '~/Downloads/**' --note "tidy downloads"
```

Note that standing ALLOW rules only take effect in `supervised` or `autonomous`
mode — in `assisted`, everything asks by design.

---

## "No model is reachable"

```bash
assistant doctor        # tells you exactly which and why
```

| Cause | Fix |
| --- | --- |
| No API key | `export ANTHROPIC_API_KEY=sk-ant-...` then add it to `~/.zshrc` |
| Library missing | Follow the path `assistant doctor` prints — it names the exact interpreter |
| Cloud switched off | `assistant config set ai.allow_cloud true` |
| Ollama not running | `ollama serve`, and `ollama pull llama3.1` |

Everything except the thinking part works with no model at all: indexing,
search, duplicates, memory, permissions, logs.

---

## "Calendar / Reminders / Notifications do not work"

macOS has not granted Apple Events permission yet.

```bash
assistant doctor --request-permissions      # run this FROM THE TERMINAL
```

macOS shows its consent dialog to whichever program asked. The background
service has no window, so a request from it fails silently. Running from the
Terminal puts the dialog somewhere you can click Allow.

To check or change afterwards: **System Settings → Privacy & Security →
Automation**, find your Terminal, and tick the apps.

If there is no entry for your Terminal at all, macOS has not asked yet — run the
command above.

---

## "The background service will not start"

```bash
assistant daemon status
assistant daemon restart
```

| Message | Meaning |
| --- | --- |
| `Input/output error` | It is already loaded. Use `assistant daemon restart`. |
| `Operation not permitted` | Load it by hand: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalaios.assistantd.plist` |
| `already running` | It is fine. `assistant daemon status` to confirm. |

**You do not need the daemon.** Every command works without it — the CLI does
the work itself. You only lose scheduled background work.

To see why it died:

```bash
cat ~/Library/Logs/PersonalAIOS/daemon.err.log
```

---

## "Everything stopped working"

Check the emergency brake:

```bash
assistant status        # says loudly if it is engaged
assistant resume
```

---

## "The index is empty or search finds nothing"

```bash
assistant index status
```

- `files: 0` → nothing indexed. Grant a folder, then `assistant index build`.
- Files indexed but no matches → this is **keyword** search. It does not match
  synonyms: searching "car" will not find "automobile". Try the actual words in
  the document.
- `unreadable: N` → normal. Images, videos and unusual formats are indexed by
  name only. For PDFs: `brew install poppler`, then `assistant index build --full`.

---

## "It says it did something but nothing happened"

It should never do this — if it does, that is a bug worth reporting. Check what
actually ran:

```bash
assistant logs --limit 20
```

`decision: deny` means it was refused (the reason is in the same row).
`needs_approval` means it is waiting: `assistant approvals`.

---

## "Something is corrupt / I want to start over"

Keep your files, reset the assistant:

```bash
assistant stop
mv ~/Library/Application\ Support/PersonalAIOS/db/assistant.db \
   ~/Library/Application\ Support/PersonalAIOS/db/assistant.db.old
assistant doctor          # recreates the database
```

You lose memory, the index and the audit log. Your documents, settings and
permissions are untouched (permissions live in the database, so re-grant them).

Complete reset:

```bash
./uninstall.sh --purge-data
./install.sh
```

---

## Reading the logs

```bash
assistant logs                          # recent activity
assistant logs --limit 100
assistant logs --event 'policy.*'       # just permission decisions
assistant logs --event 'tool.*'         # just tool runs
```

The raw records are at `~/Library/Logs/PersonalAIOS/audit.jsonl`, one JSON
object per line. Secrets are redacted before writing.

Application logs (for crashes) are at
`~/Library/Logs/PersonalAIOS/assistant.log`.

---

## Still stuck

```bash
assistant doctor --json > /tmp/report.json
assistant logs --limit 50 >> /tmp/report.json
```

That report contains no file contents and no secrets — it is safe to share.
