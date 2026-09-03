# INSTALL

Written for someone who has never used a Terminal. If you have, skip to
[the short version](#the-short-version).

---

## The short version

```bash
./install.sh
assistant permissions grant ~/Documents
assistant index build
export ANTHROPIC_API_KEY=sk-ant-...     # or use Ollama, see step 6
assistant ask "what is in my Documents?"
```

---

## The long version

### Step 1 — Open the Terminal

Press `⌘ + Space`, type `Terminal`, press Return. A window appears with some
text and a blinking cursor. This is where you type commands.

**What am I looking at?** A prompt, usually ending in `$` or `%`. You type after
it and press Return. Nothing happens until you press Return.

**What if I make a mistake?** Nothing breaks. Press `Ctrl + C` to cancel the
current line and start again.

### Step 2 — Get the files

If you were given a folder, drag it somewhere sensible like your Documents.
Then tell the Terminal to go into it:

```bash
cd ~/Documents/personal-ai-os
```

**What is `cd`?** "Change directory" — it moves the Terminal into a folder, like
double-clicking one in Finder. `~` is shorthand for your home folder.

**What should I see?** The prompt changes to show where you are. Type `ls` and
press Return to list what is in the folder. You should see `install.sh`,
`README.md` and a `src` folder.

### Step 3 — Run the installer

```bash
./install.sh
```

**What is `./`?** It means "the thing in this folder", as opposed to a program
installed system-wide. It is a safety feature: you have to be explicit.

**What should I see?** A series of steps with `==>` markers, ending in
`Installed.` and a list of what to do next. It takes about 30 seconds.

**Do NOT type `sudo`.** The installer refuses to run that way on purpose. `sudo`
means "do this as the all-powerful administrator", and the whole design of this
assistant is that it has exactly your permissions and no more.

**What if it stops with an error?**

| Message | What to do |
| --- | --- |
| `No Python 3.9+ was found` | Run `xcode-select --install`, click through the dialog, wait, then try again. |
| `SQLite was built without FTS5` | Run `brew install python`, then `./install.sh` again. If you do not have Homebrew, get it from https://brew.sh |
| `Permission denied` | Run `chmod +x install.sh` then try again. |
| `macOS 12 or newer is required` | The assistant needs a newer macOS than you have. |

### Step 4 — Make the `assistant` command work

The installer may warn that `~/.local/bin` is not on your PATH.

**What does that mean?** Your Mac looks for commands in a fixed list of folders.
The installer put `assistant` in a folder that is not on that list yet, so
typing `assistant` gets "command not found".

Fix it once:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**What should I see?** `assistant --version` now prints `Personal AI OS 1.0.0`.

### Step 5 — Give it access to something

This is the important step. **A fresh install can read nothing at all.**

```bash
assistant permissions grant ~/Documents
assistant permissions grant ~/Downloads --mode readwrite
```

The first gives read-only access. The second allows changes too — use it for
folders where you actually want it to tidy up.

**Start small.** Grant one folder, see how it behaves, grant more later. You can
withdraw access at any time:

```bash
assistant permissions revoke ~/Downloads
```

### Step 6 — Build the file catalogue

```bash
assistant index build
```

**What is this doing?** Reading the files in your granted folders and writing a
searchable card for each one. The first run on a large folder can take a minute;
after that it only looks at what changed.

**What should I see?** A summary: how many files were scanned, added, and how
many could not be read (images and unusual formats — that is normal).

Try it:

```bash
assistant index search "something you know is in your files"
```

Everything so far works with **no AI model at all**.

### Step 7 — Give it a brain

Pick one.

**Option A — cloud (most capable).** Get a key from
https://console.anthropic.com, then:

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-your-key-here' >> ~/.zshrc
source ~/.zshrc
assistant doctor
```

Your conversations — which can include excerpts of files you ask about — are
sent to Anthropic. Nothing else is: not your file listing, not your
permissions, not your logs.

**Option B — local (nothing leaves your Mac).** Install Ollama from
https://ollama.com, then:

```bash
ollama pull llama3.1
assistant config set ai.roles.reasoning ollama
assistant config set ai.roles.planning ollama
assistant config set ai.allow_cloud false      # belt and braces
```

Slower and less capable, but completely private.

**Option C — both.** Point the everyday roles at the local model and only the
hard reasoning at the cloud. See [PERMISSIONS.md](PERMISSIONS.md).

### Step 8 — Check everything

```bash
assistant doctor
```

**What should I see?** A list of checks. `OK` is good. `WARN` means an *optional*
feature is unavailable and tells you how to enable it — warnings are not
breakages. `FAIL` means something needs fixing, and it will tell you what.

### Step 9 — macOS permission dialogs

If you want Calendar, Reminders or Notifications, run this **from the Terminal**:

```bash
assistant doctor --request-permissions
```

macOS will show consent dialogs. Click Allow.

**Why from the Terminal specifically?** macOS shows the dialog to the program
that asked. The background service has no window, so the dialog would have
nowhere to appear and the request would just fail. Running it from the Terminal
puts the dialog somewhere you can actually click it.

### Step 10 — Try it

```bash
assistant status
assistant ask "summarise what is in my Documents folder"
```

---

## Where things end up

| What | Where |
| --- | --- |
| Program | `~/Library/Application Support/PersonalAIOS/app` |
| Settings | `~/Library/Application Support/PersonalAIOS/config.json` |
| Database | `~/Library/Application Support/PersonalAIOS/db/assistant.db` |
| Logs | `~/Library/Logs/PersonalAIOS/` |
| Command | `~/.local/bin/assistant` |
| Login item | `~/Library/LaunchAgents/com.personalaios.assistantd.plist` |

Nothing is installed outside your home folder. Nothing needs administrator
rights.

---

## Installing without the background service

```bash
./install.sh --no-daemon
```

Everything works; you just do not get scheduled background work (automatic index
refresh, memory decay). Start it later with `assistant daemon install`.

## Installing with no internet

```bash
./install.sh --no-cloud
```

Skips downloading the Anthropic library. Use Ollama, or add it later.
