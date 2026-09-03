# STATUS — what actually works

No marketing. If a row says NOT IMPLEMENTED, the feature does not exist and
nothing in the code pretends otherwise.

Legend: **✅ works** · **⚠️ works with a caveat** · **❌ not implemented**

---

## Core

| Feature | State | Notes |
| --- | --- | --- |
| Permission engine (11-step decision) | ✅ | 48 tests |
| Scopes, symlink and prefix containment | ✅ | |
| Sealed forbidden list | ✅ | Non-overridable, includes its own data directory |
| Approval queue, expiry, argument binding | ✅ | |
| Kill switch | ✅ | File-based, works even if the daemon has hung |
| Audit log (SQLite + JSONL, append-only) | ✅ | Secrets redacted |
| Secret redaction | ⚠️ | Pattern-based. Catches common shapes; an unusual format could slip through |
| Config with validation | ✅ | Refuses invalid values, saves nothing on failure |
| SQLite schema + atomic migrations | ✅ | |

## Tools

| Feature | State | Notes |
| --- | --- | --- |
| File read / search / stat / list | ✅ | |
| File create / write / move / copy / mkdir | ✅ | Never overwrites; writes make a backup |
| File trash | ✅ | Recoverable |
| File delete (single file) | ✅ | Permanent; always asks; refuses folders |
| Folder delete | ❌ | Deliberately not offered. Use trash |
| Shell command | ⚠️ | Allow-list only, no shell, no pipes, no redirection. Read-only tools by default |
| System info | ✅ | |
| 30 tools total, 9 macOS-only | ✅ | `assistant tools` |

## File intelligence

| Feature | State | Notes |
| --- | --- | --- |
| Incremental indexing | ✅ | Skips unchanged files by size + mtime |
| Text: `.txt .md .csv .json` + 30 more | ✅ | |
| `.docx .pptx .xlsx` | ✅ | Standard library only — no dependency |
| `.html` | ✅ | Scripts and styles stripped |
| `.rtf .doc` | ⚠️ | macOS only (uses `textutil`) |
| `.pdf` | ⚠️ | Needs `pdftotext` (`brew install poppler`) or `pypdf`. Says so plainly when absent |
| `.pages .key .numbers` | ❌ | Indexed by filename only |
| Keyword search (FTS5 + BM25) | ✅ | Ranked, with excerpts |
| "Related documents" | ⚠️ | Shared distinctive vocabulary, **not** meaning |
| **Semantic / embedding search** | ❌ | Not implemented. "car" will not match "automobile" |
| Duplicate detection | ✅ | SHA-256; reports only, deletes nothing |
| OCR on images/scans | ❌ | Not implemented |

## Memory

| Feature | State | Notes |
| --- | --- | --- |
| Kinds, provenance, confidence | ✅ | |
| Exponential decay, pinning | ✅ | |
| Eviction of weakest at the limit | ✅ | Pinned entries survive |
| List / search / edit / forget / export | ✅ | |
| Disable entirely | ✅ | Recall is skipped, not filtered |
| Refuses to store secrets | ⚠️ | Pattern-based, same caveat as above |

## Reasoning and planning

| Feature | State | Notes |
| --- | --- | --- |
| Multi-strategy generation | ✅ | Needs a model |
| Utility scoring (6 weighted criteria) | ✅ | Hand-checkable; tested to exact values |
| Hard constraints from your DENY rules | ✅ | Forbidden strategies are never proposed |
| Constructive/destructive interference | ✅ | Jaccard-weighted agreement; tested |
| Monte-Carlo simulation, expected value + volatility | ✅ | Matches analytic values in tests |
| Risk ceiling with honest forced fallback | ✅ | Says when it had to pick a risky option |
| Bayesian (Beta-Binomial) updating | ✅ | Exact formula tested |
| Decoherence (confidence ages) | ✅ | Decays toward uncertainty, not toward zero |
| Beam search over multi-step plans | ✅ | Beats greedy on delayed payoff, tested |
| Plan persistence, alternatives recorded | ✅ | |
| **Actual quantum computation** | ❌ | **None. Zero. This is classical arithmetic and the code says so** |
| Automatic step-by-step plan execution | ❌ | Plans are proposed; you drive execution through `ask` |

## AI providers

| Feature | State | Notes |
| --- | --- | --- |
| Anthropic (`claude-opus-5`) | ✅ | Official SDK, adaptive thinking, streaming, refusal fallback |
| Ollama (fully local) | ✅ | |
| Any OpenAI-compatible endpoint | ✅ | |
| Offline stub | ✅ | Explains what is missing; never fabricates an answer |
| Role router (6 roles) | ✅ | |
| Unavailable provider fallback | ✅ | Returns setup instructions, not a guess |
| Vision / image understanding | ❌ | The role exists; no image tool feeds it yet |
| Token/cost accounting | ⚠️ | Recorded in the `usage` table; no reporting command yet |

## macOS integration

| Feature | State | Notes |
| --- | --- | --- |
| AppleScript bridge (argv-safe) | ✅ | Untestable end-to-end off a Mac; the safety property is tested |
| Calendar read / create | ⚠️ | Needs Automation permission |
| Reminders read / create | ⚠️ | Needs Automation permission |
| Notifications | ⚠️ | Needs notification permission |
| Clipboard read | ✅ | macOS only |
| Open application | ✅ | macOS only |
| Spotlight search | ✅ | Results filtered through the path guard |
| TCC permission probing | ✅ | By trying a harmless operation, never by reading Apple's databases |
| launchd login service | ✅ | Modern `bootstrap`/`bootout` form |
| **Full Disk Access** | ❌ | **Deliberately never requested** |
| Mail / Messages history | ❌ | Would require Full Disk Access |
| Browser history / bookmarks | ❌ | Same |
| Accessibility app control | ❌ | Fragile across app updates |
| Contacts | ⚠️ | Capability defined; no tool implemented yet |
| Shortcuts integration | ❌ | Not implemented |
| Signed `.app` bundle, menu-bar UI | ❌ | Needs an Apple Developer ID |

## Daemon and CLI

| Feature | State | Notes |
| --- | --- | --- |
| Unix-socket daemon (0600) | ✅ | Ownership checked at startup |
| Concurrent clients | ✅ | Tested with 8 at once |
| Survives malformed requests | ✅ | |
| Scheduler (4 built-in jobs) | ✅ | No arbitrary-command job type, on purpose |
| CLI falls back when daemon is down | ✅ | |
| Full command set | ✅ | |
| `--json` for scripting | ✅ | |

## Install and diagnostics

| Feature | State | Notes |
| --- | --- | --- |
| `install.sh` | ✅ | Verified by running it, not by reading it |
| `uninstall.sh` (keeps data) | ✅ | |
| `uninstall.sh --purge-data` | ✅ | Never touches user documents |
| `doctor` (11 checks) | ✅ | Every failure names its fix |
| macOS permission request flow | ✅ | Prompts from the Terminal where they can be answered |

---

## Testing

273 automated tests. They run on macOS *and* Linux, because everything except
the macOS bridges is platform-independent — which also means the security layer
is tested on every push regardless of platform.

```
tests/test_security.py        48   scopes, traversal, symlinks, injection, policy, shell
tests/test_tools.py           25   validation, gating, swapped-argument attack
tests/test_index.py           19   extraction, indexing, search, duplicates
tests/test_memory.py          27   secret refusal, decay, user control
tests/test_planner.py         27   the arithmetic, checked against exact values
tests/test_orchestrator.py    22   the agent loop, hijacked-model simulation
tests/test_macos.py           31   AppleScript injection safety, plists, dates
tests/test_daemon.py          22   real socket, concurrency, scheduler
tests/test_cli.py             28   the commands people actually type
tests/test_packaging.py        5   structural invariants (see below)
tests/test_install.py         12   the real installer, end to end
tests/test_ai.py               7   provider honesty
```

`test_packaging.py` enforces four project-wide invariants: no exported name
shadows a submodule, every `__all__` entry exists, the security layer never
imports what it guards, and the entire codebase parses as Python 3.9 (what macOS
ships).

## What was found and fixed while building

Listed because "we wrote tests" means nothing without saying what they caught:

- SQLite `executescript()` auto-commits, so migrations were not atomic.
- Approval expiry used `<` instead of `<=`, so a zero-second timeout never expired.
- The secret detector only matched `password = x`, missing the far more common
  "my password is x" — so a password could reach long-term memory.
- `extract_json` always looked for `[` first, so an object containing an array
  was parsed as just the inner array, silently losing every other field.
- `assistant.orchestrator.reflect` and `assistant.cli.main` were functions
  shadowing the modules of the same name.
- `signal.signal()` raises outside the main thread, so an embedded daemon died
  at startup.
- Policy denials carried the generic error code rather than `policy.denied`.
- Long paths pushed the MODE column off the permissions table, hiding whether a
  folder was read-only.
- An error message told users to run `assistant doctor --fix`, a flag that does
  not exist, and hardcoded a macOS path on every platform.
