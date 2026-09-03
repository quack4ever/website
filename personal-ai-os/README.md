# Personal AI OS

**An AI assistant that lives on your Mac, understands your files, plans your
work, and can only ever do what you have explicitly allowed.**

It is not a website. It is not a chat window in a browser. It is a background
service plus a command you type, wired into macOS through Apple's own supported
interfaces, with a permission system standing between the AI and your computer.

```
$ assistant                       # just talk to it - no quotes, no command names
you › what am I missing on my science project?
```

Or use the **desktop app** — menu bar icon, click-to-approve, folder picker:

```
$ cd mac-app && ./build.sh --install --run
```

Everything is also available as one-shot commands:

```
$ assistant ask "help me organise my semester"
$ assistant permissions grant ~/Documents
$ assistant stop          # emergency brake, works instantly
```

---

## Start here if you have never programmed

That is fine. This section assumes nothing.

**What is the Terminal?** An app already on your Mac (press `⌘ + Space`, type
"Terminal", press Return). It lets you type commands instead of clicking. That
is all it is — a text-based way to ask your computer to do things.

**What are we installing?** Two things:

1. A **daemon** — a small program that stays quietly awake in the background,
   waiting for instructions. Think of it as a little robot that lives in your
   computer and never sleeps.
2. A **command** called `assistant` — the way you talk to that robot.

**What is a permission?** Permission to open a specific door. This robot starts
with **zero** keys. It cannot read a single file until you hand it one:

```
assistant permissions grant ~/Documents
```

That is the whole idea. The robot asks; a security guard checks your rulebook;
the guard works for you, not the robot.

---

## Install

```bash
git clone <this repository>
cd personal-ai-os
./install.sh
```

Then follow the four steps it prints. Full detail in **[INSTALL.md](INSTALL.md)**.

Requirements: macOS 12 or newer, and a Python 3.9+ (your Mac already has one).
Nothing else is mandatory.

---

## The five-minute tour

```bash
# 1. Give it something to look at. Right now it can read NOTHING.
assistant permissions grant ~/Documents
assistant permissions grant ~/Downloads --mode readwrite

# 2. Build a searchable catalogue of those files.
assistant index build

# 3. Look around - all of this works with no AI model at all.
assistant index search "chemistry"
assistant duplicates
assistant status

# 4. Give it a brain. Either:
export ANTHROPIC_API_KEY=sk-ant-...              # cloud, most capable
# ...or install Ollama from https://ollama.com   # fully local, private
assistant config set ai.roles.reasoning ollama

# 5. Ask it something real.
assistant ask "which of my chemistry files mention titration?"
assistant plan "help me get ready for my midterm"

# At any moment:
assistant logs         # everything it has ever done
assistant approvals    # anything waiting for your yes
assistant stop         # halt everything, immediately
```

---

## What it can actually do

**Understand your files.** Indexes the folders you granted, extracts text from
`.txt`, `.md`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.csv`, code, and (with a
helper installed) `.pdf`. Full-text search with proper relevance ranking, plus
"find documents related to this one" and byte-exact duplicate detection.

**Think in more than one direction.** Given a goal, it generates several
genuinely different strategies, scores each on reliability, impact,
reversibility, speed, your effort and your preferences, simulates them to get an
expected value *and* a risk number, and shows you the ranked table — including
what it rejected and why. See [the reasoning section below](#quantum-inspired-reasoning).

**Remember you.** Preferences, projects, deadlines, how you like to work. You
can list, search, edit, delete or switch off every bit of it. It refuses to
store passwords and keys.

**Act on your Mac** — read and write files, tidy folders, create calendar events
and reminders, send notifications, open apps, run allow-listed commands — always
through the permission system.

**Explain itself.** Every action is logged with what it did, when, which tool,
which rule allowed it, and which files it touched.

---

## What it deliberately will NOT do

Being honest about this matters more than a longer feature list.

| Not implemented | Why |
| --- | --- |
| Read your Mail or Messages history | Needs Full Disk Access, which macOS attaches to the *Python interpreter* — granting it would give every Python script on your Mac the same power, forever. Not worth it. |
| Read Safari history or browser cookies | Same reason. |
| Click buttons in apps for you | Needs Accessibility permission and breaks with every app update. Maybe later, honestly labelled. |
| Semantic / synonym search | The search is keyword-based (BM25). It will not match "car" to "automobile". Real semantic search needs an embedding model; it is on the roadmap and is *not* claimed here. |
| Modify its own code | Deliberately impossible. It can suggest changes; it cannot make them. |
| Delete a whole folder permanently | Only single files, and only after approval. Folders go to the Trash where you can get them back. |

If a feature is not listed as working, assume it does not exist. See
**[docs/STATUS.md](docs/STATUS.md)** for the full, blunt feature-by-feature table.

---

## How safe is it, really?

The honest answer is: **safe because of its structure, not because of its
promises.**

The AI has no hands. It cannot touch a file, run a command, or open an app. All
it can do is *request* a named tool with typed arguments. Every request goes
through one function — `registry.execute()` — which asks the policy engine
first. The policy engine reads permissions from a database on your disk that
**nothing the AI produces can write to**.

That is the important part. A completely hijacked model — one that has been fed
a malicious document and is now working for an attacker — still cannot grant
itself access to anything. It can ask. The answer is still no.

On top of that:

- **Zero access by default.** A fresh install can read nothing.
- **Two locks on every path.** It must be inside a folder you granted, *and* not
  on a permanently-blocked list (SSH keys, keychains, browser data, its own
  rulebook) that no setting can unlock.
- **CRITICAL actions always ask.** Sending a message, sending mail, spending
  money, changing security settings. There is no configuration that turns this
  off.
- **Untrusted content is labelled.** Text from your files reaches the model
  wrapped in a marker that says "this is data, not instructions", with known
  injection attempts flagged.
- **Approvals are bound to the exact action.** Approving "delete junk.txt"
  cannot be replayed to delete something else — the arguments are fingerprinted.
- **One command stops everything.** `assistant stop`.

Read **[SECURITY.md](SECURITY.md)** for the threat model and the honest
limitations.

---

## Quantum-inspired reasoning

The brief asked for the assistant to "think like a quantum computer". Here is
the honest version:

> **Your Mac has no qubits. This performs no quantum computation.** What it
> borrows is the *shape* of quantum reasoning, in ordinary arithmetic you can
> check by hand.

| Quantum idea | What actually runs |
| --- | --- |
| Superposition | several candidate strategies kept alive at once, each weighted |
| Amplitude | a utility score per strategy |
| Probability | softmax over those scores |
| Destructive interference | a strategy breaking one of your hard rules is zeroed |
| Constructive interference | strategies that agree with other strong strategies gain weight |
| Measurement | choosing one to run — and recording why |
| Decoherence | confidence fading as assumptions age |
| Bayesian update | re-weighting as steps actually succeed or fail |

A real run, on the brief's own example:

```
STRATEGY                               UTILITY P(best) E[val]   RISK  AGREE
----------------------------------------------------------------------------
Weekly review habit: light structure     0.828    32.5%  0.755  0.202  0.156
Deadline-first: calendar from due dates  0.805    32.8%  0.610  0.387  0.248
Subject-folders: reorganise by class     0.698    21.3%  0.536  0.348  0.112
Full rebuild: re-file and re-plan all    0.493    13.4%  0.096  0.714  0.404
Clean slate: archive everything old      0.000     0.0%  0.000  1.000  0.000  <- RULED OUT: never delete anything
```

Note the last row: your standing rule "never delete anything" annihilated that
strategy before it was ever proposed. And note "Full rebuild" — it has the
*highest* agreement with other strategies (0.404) but the lowest utility, so
interference correctly failed to rescue a bad idea.

Then, after two simulated failures, Bayesian updating dropped the leader's
per-step confidence from 0.95 to 0.63 and the planner **changed its mind**.

---

## The documentation

| File | What is in it |
| --- | --- |
| **[INSTALL.md](INSTALL.md)** | Installing, step by step, assuming no experience |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | How the whole thing is built, and why |
| **[SECURITY.md](SECURITY.md)** | Threat model, defenses, and honest weaknesses |
| **[PERMISSIONS.md](PERMISSIONS.md)** | Every permission, what it means, how to set it |
| **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** | When something does not work |
| **[DEVELOPMENT.md](DEVELOPMENT.md)** | Reading and changing the code |
| **[UNINSTALL.md](UNINSTALL.md)** | Removing it cleanly |
| **[mac-app/README.md](mac-app/README.md)** | The desktop app |
| **[docs/STATUS.md](docs/STATUS.md)** | What works, what does not, no marketing |

---

## Commands

```
assistant ask "..."                  ask a question or request something
assistant plan "..."                 compare strategies for a goal
assistant status                     what it can do and has been doing
assistant doctor                     check the installation, explain problems

assistant permissions                what it may do right now
assistant permissions grant PATH     allow access to a folder
assistant permissions revoke PATH    withdraw access
assistant permissions capabilities   every possible action, with risk levels
assistant permissions rule add ...   write a standing rule
assistant permissions macos          check macOS app permissions

assistant approvals                  what is waiting for your yes
assistant approve ID / deny ID       answer it
assistant stop / resume              emergency brake

assistant memory list|search|add|edit|forget|export
assistant index build|status|search|related|clear
assistant duplicates
assistant config show|set
assistant logs
assistant tools
assistant daemon start|stop|status|install|uninstall
```

---

## Licence and status

This is a working system, built and tested end to end: 273 automated tests
covering the security boundaries, the planner's mathematics, the tool layer,
the daemon, the CLI, and the installer itself.

The parts that are not finished are listed in **[docs/STATUS.md](docs/STATUS.md)**
rather than hidden.
