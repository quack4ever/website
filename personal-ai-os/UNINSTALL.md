# UNINSTALL

## Remove the program, keep your data

```bash
./uninstall.sh
```

This:
- stops the background service and removes it from your login items
- deletes the `assistant` command
- deletes the program and its virtual environment
- **keeps** your settings, memory, file index and audit log

Reinstall later and it picks up exactly where it left off — same permissions,
same memories, same index.

## Remove everything

```bash
./uninstall.sh --purge-data
```

You will be asked to type `DELETE` to confirm. This additionally erases:
- `config.json` — your settings and permissions
- `db/assistant.db` — memory, file index, plans, audit log
- `backups/` — copies of files the assistant edited
- the log files

## Your own documents are never touched

Neither command reads, moves or deletes any of your files. Not in either mode.
The assistant only ever stores its *own* data in its *own* folder.

If the assistant edited a file for you, a backup is in
`~/Library/Application Support/PersonalAIOS/backups/`. **Copy anything you want
out of there before running `--purge-data`.**

## By hand, if the script is gone

```bash
launchctl bootout "gui/$(id -u)/com.personalaios.assistantd"
rm -f ~/Library/LaunchAgents/com.personalaios.assistantd.plist
rm -f ~/.local/bin/assistant
rm -rf ~/Library/Application\ Support/PersonalAIOS
rm -rf ~/Library/Logs/PersonalAIOS
```

That is everything. Nothing is installed outside your home folder, nothing needs
administrator rights to remove, and no system files are modified.

## macOS permissions left behind

Uninstalling does not clear the Automation permissions you granted. Remove them
in **System Settings → Privacy & Security → Automation**, under the entry for
your Terminal.

## Check it is gone

```bash
which assistant                                  # should print nothing
launchctl print "gui/$(id -u)/com.personalaios.assistantd"   # should error
ls ~/Library/Application\ Support/PersonalAIOS   # should not exist (after --purge-data)
```
