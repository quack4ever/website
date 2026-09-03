# PERMISSIONS

Everything the assistant is allowed to do, and how to change it.

---

## The one-minute model

Three dials control everything:

1. **Scopes** — which folders exist, as far as it is concerned. Default: none.
2. **Autonomy mode** — how much it may do without asking. Default: ask always.
3. **Rules** — your written policy ("you may tidy Downloads, never delete").

Plus one thing you cannot change: a **sealed list** of files nothing can reach.

```bash
assistant permissions        # see all of it at once
```

---

## 1. Scopes — which folders it can see

```bash
assistant permissions grant ~/Documents                  # read only
assistant permissions grant ~/Downloads --mode readwrite # may change things
assistant permissions revoke ~/Downloads
```

A path must resolve *inside* a granted folder. Symlinks are followed first, so a
shortcut pointing somewhere else does not work. `Documents-secret` does not
match a scope on `Documents`.

**Advice:** grant `~/Documents` read-only first. Add `--mode readwrite` only for
folders you actually want tidied — `~/Downloads` is the usual one.

---

## 2. Autonomy modes — how much it does without asking

```bash
assistant config set autonomy.mode supervised
```

| Mode | It will... | Runs automatically |
| --- | --- | --- |
| `observe` | answer questions only, touch nothing | nothing |
| `suggest` | describe exactly what it *would* do | nothing |
| `assisted` **(default)** | ask before every action | plain reads |
| `supervised` | tidy, create and move files quietly; ask for the rest | LOW + MEDIUM |
| `autonomous` | run your pre-approved workflows | LOW + MEDIUM, plus HIGH where a rule allows |

**CRITICAL actions always ask, in every mode.** There is no setting that changes
this.

---

## 3. Risk tiers

| Tier | Meaning | Examples |
| --- | --- | --- |
| **LOW** | looking. Reversible, harmless. | `files.read`, `files.search`, `index.query`, `memory.read`, `system.info` |
| **MEDIUM** | changes you can undo. | `files.create`, `files.write`, `files.move`, `files.copy`, `memory.write`, `calendar.read`, `notify.send` |
| **HIGH** | deleting, running commands, writing to your calendar. | `files.delete`, `files.trash`, `shell.execute`, `app.launch`, `calendar.write`, `contacts.read` |
| **CRITICAL** | irreversible, or visible to other people. | `files.bulk_delete`, `message.send`, `mail.send`, `security.change`, `purchase.make`, `publish.content` |

```bash
assistant permissions capabilities   # the full list with descriptions
```

An unknown capability is treated as CRITICAL. The system fails closed.

---

## 4. Rules — your written policy

```bash
# "You may organise my Downloads automatically, but never delete anything."
assistant permissions rule add --effect allow --capability files.move \
    --path '~/Downloads/**' --note "tidy downloads"
assistant permissions rule add --effect deny --capability files.delete \
    --note "never delete my files"

# "You may create calendar events, but always ask before inviting people."
assistant permissions rule add --effect allow --capability calendar.write \
    --note "events are fine"

assistant permissions rule list
assistant permissions rule remove 2
```

### How a decision is made

First match wins:

```
 1. emergency stop engaged?          -> DENY   (always)
 2. path on the sealed list?         -> DENY   (never overridable)
 3. a DENY rule matches?             -> DENY   (beats everything below)
 4. path outside every scope?        -> DENY
 5. write asked on a read-only scope?-> DENY
 6. capability is CRITICAL?          -> ASK    (no setting can auto-allow)
 7. mode is observe/suggest and this
    is not a plain read?             -> DENY
 8. an ASK rule matches?             -> ASK
 9. an ALLOW rule matches?           -> ALLOW  (supervised/autonomous only)
10. risk within the mode's ceiling?  -> ALLOW
11. otherwise                        -> ASK
```

**Deny always beats allow.** The default is ASK, never ALLOW.

**Standing ALLOW rules only take effect in `supervised` and `autonomous` modes.**
In the default `assisted` mode, "asks before every action" means exactly that —
a rule you wrote still stops and asks, and tells you which rule *would* have
allowed it. Turning standing rules on should be a conscious decision.

Every decision is logged: `assistant logs`.

---

## 5. The sealed list — what nothing can ever reach

No scope, no rule, no mode unlocks these:

```
~/.ssh  ~/.gnupg  ~/.aws  ~/.azure  ~/.kube  ~/.docker  ~/.netrc
~/Library/Keychains          /Library/Keychains
~/Library/Application Support/com.apple.TCC
~/Library/Messages  ~/Library/Mail  ~/Library/Safari  ~/Library/Cookies
~/Library/Application Support/Google/Chrome (and Firefox, Brave)
/System  /private/var/db  /Library/Security  /etc/sudoers
the assistant's own data directory
```

Plus any file named: `id_rsa`, `id_ed25519`, `*.pem`, `*.p12`, `*.keychain`,
`.env`, `.netrc`, `credentials`, `authorized_keys`, `known_hosts`, …

Trying to grant a scope on one of these is refused.

---

## 6. Approvals

When something needs your yes:

```bash
assistant approvals            # see what is waiting
assistant approve a1b2c3d4e5f6
assistant deny a1b2c3d4e5f6
```

Approvals are bound to the exact arguments. Approving "delete junk.txt" cannot
be reused to delete something else. Unanswered approvals expire — silence is
never consent.

---

## 7. The emergency stop

```bash
assistant stop                 # everything halts, immediately
assistant resume
```

This writes a file that every tool checks before acting, so it works even if the
background service has hung.

---

## 8. macOS app permissions

Separate from all of the above, and controlled by macOS itself:

```bash
assistant permissions macos                # what is granted
assistant doctor --request-permissions     # run from Terminal to be asked
```

| Permission | Needed for |
| --- | --- |
| Automation → Calendar | reading and creating events |
| Automation → Reminders | reading and creating reminders |
| Automation → Finder | moving files to the Trash properly |
| Notifications | showing you notifications |

**Full Disk Access is never requested.** See [SECURITY.md](SECURITY.md) §3.

If a dialog never appears, run the command **from the Terminal** — a background
service has no window for macOS to show it in.

---

## 9. Choosing where your data goes

```bash
assistant config set ai.allow_cloud false          # nothing ever leaves the Mac
assistant config set ai.roles.reasoning ollama     # use a local model
assistant config set ai.disclose_cloud_calls true  # log every outbound call
assistant config set memory.enabled false          # remember nothing
```

Roles you can point at different models: `reasoning`, `planning`, `fast`,
`coding`, `vision`, `local`. A common setup is everyday work local, hard
reasoning in the cloud.
