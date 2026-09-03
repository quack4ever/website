"""The command line: how you actually talk to the assistant.

TWO WAYS TO THE SAME PLACE
--------------------------
Every command first tries to reach the background daemon over its socket.  If
the daemon is not running, the CLI does the work itself, in this process.
Both routes call the same handler functions, so the answer is identical either
way - you just lose scheduled background work when the daemon is off.

That fallback matters: a broken or unloaded daemon must never make the whole
assistant unusable.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from .. import paths
from ..errors import AssistantError
from ..version import __version__
from . import render

# Commands that must never be routed through the daemon: they manage the
# daemon itself, or must work when it is dead.
LOCAL_ONLY = {"daemon", "version", "help"}


def _call(command: str, args: Optional[Dict[str, Any]] = None,
          force_local: bool = False) -> Any:
    """Run one operation, preferring the daemon, falling back to in-process."""
    from ..daemon import handlers
    from ..daemon.server import Client

    if not force_local:
        client = Client()
        if client.available():
            try:
                return client.call(command, args)
            except AssistantError as exc:
                # A daemon that is genuinely down should not stop us; a daemon
                # that answered with a real error should be reported as-is.
                if getattr(exc, "code", "") != "daemon.failed":
                    raise
    return handlers.dispatch(command, args or {})


def _output(data: Any, as_json: bool, renderer=None) -> int:
    if as_json:
        print(render.as_json(data))
        return 0
    if renderer:
        print(renderer(data))
    else:
        print(render.as_json(data))
    return 0


# ==========================================================================
# renderers
# ==========================================================================
def _render_status(data: Dict[str, Any]) -> str:
    lines = [render.heading("Personal AI OS %s" % data.get("version", "")), ""]
    kill = data.get("kill_switch")
    if kill:
        lines.append(render.paint(
            "  EMERGENCY STOP IS ENGAGED (%s). Nothing will run until you type "
            "'assistant resume'." % kill.get("reason"), "red", "bold"))
        lines.append("")
    lines.append("  Autonomy mode:  %s  (runs %s automatically on its own)"
                 % (render.paint(data.get("autonomy_mode", "?"), "bold"),
                    data.get("auto_approves_up_to")))

    scopes = data.get("scopes") or []
    if scopes:
        lines.append("  Folders granted:")
        for scope in scopes:
            lines.append("      %s  (%s)" % (scope["path"], scope["mode"]))
    else:
        lines.append("  Folders granted: " + render.paint("none yet", "yellow")
                     + "  ->  assistant permissions grant ~/Documents")

    lines.append("  Standing rules: %d" % data.get("rules", 0))
    pending = data.get("pending_approvals", 0)
    lines.append("  Waiting for you: %s"
                 % (render.paint("%d approval(s)  ->  assistant approvals" % pending,
                                 "yellow") if pending else "nothing"))

    index = data.get("index") or {}
    lines.append("")
    lines.append("  Files indexed:  %s  (%s readable as text, %s unreadable)"
                 % (index.get("files", 0), index.get("with_extracted_text", 0),
                    index.get("unreadable", 0)))
    memory = data.get("memory") or {}
    lines.append("  Memories:       %s%s"
                 % (memory.get("total", 0),
                    "" if memory.get("enabled", True)
                    else render.paint("  (memory is switched OFF)", "yellow")))

    models = (data.get("models") or {}).get("roles") or {}
    lines.append("")
    lines.append("  Models:")
    for role, info in models.items():
        mark = render.paint("ready", "green") if info.get("available") \
            else render.paint("unavailable", "yellow")
        lines.append("      %-10s %-14s %s  %s"
                     % (role, info.get("provider", "?"), mark,
                        "" if info.get("available") else info.get("reason", "")[:46]))

    daemon = data.get("launch_agent") or {}
    lines.append("")
    lines.append("  Background service: %s"
                 % ("running (pid %s)" % daemon.get("pid") if daemon.get("pid")
                    else "loaded" if daemon.get("loaded")
                    else "not running  ->  assistant daemon start"))
    return "\n".join(lines)


def _render_doctor(data: Dict[str, Any]) -> str:
    lines = [render.heading("Health check"), ""]
    for check in data.get("checks", []):
        lines.append("%s  %s" % (render.status_mark(check["status"]),
                                 render.paint(check["name"], "bold")))
        lines.append(render.wrap(check["detail"], indent=8))
        if check.get("fix"):
            lines.append(render.paint(render.wrap(check["fix"], indent=8), "dim"))
        lines.append("")
    lines.append(render.rule())
    summary = data.get("summary", "")
    lines.append(render.paint(summary, "green" if data.get("healthy") else "red"))
    if data.get("warnings"):
        lines.append(render.paint(
            "%d warning(s): these are optional features, not breakages."
            % data["warnings"], "dim"))
    return "\n".join(lines)


def _render_permissions(data: Dict[str, Any]) -> str:
    lines = [render.heading("Permissions"), ""]
    lines.append("  Autonomy mode: %s" % render.paint(data.get("mode", "?"), "bold"))
    lines.append("  Runs automatically without asking: up to %s risk"
                 % data.get("auto_approves_up_to"))
    lines.append("  Standing ALLOW rules apply: %s"
                 % ("yes" if data.get("standing_rules_apply")
                    else "no (only in supervised/autonomous mode)"))
    lines.append("")
    lines.append(render.heading("  Folders it may touch"))
    scopes = data.get("scopes") or []
    if scopes:
        lines.append(render.table([[s["path"], s["mode"], s.get("note", "")]
                                   for s in scopes],
                                  headers=["PATH", "MODE", "NOTE"]))
    else:
        lines.append("  none - it can read nothing until you grant a folder")
    lines.append("")
    lines.append(render.heading("  Your rules"))
    rules = data.get("rules") or []
    if rules:
        lines.append(render.table(
            [[r["id"], r["effect"], r["capability"], r.get("path_glob") or "(any)",
              r.get("note", "")] for r in rules],
            headers=["ID", "EFFECT", "CAPABILITY", "PATH", "NOTE"]))
    else:
        lines.append("  none - behaviour comes from the autonomy mode alone")
    lines.append("")
    lines.append(render.heading("  Permanently blocked, whatever you grant"))
    forbidden = (data.get("forbidden") or {}).get("forbidden_prefixes", [])
    lines.append(render.wrap(", ".join(forbidden[:12]) + " ...", indent=2))
    return "\n".join(lines)


def _render_capabilities(data: Dict[str, Any]) -> str:
    lines = [render.heading("Everything the assistant can ever ask to do"), ""]
    rows = [[c["name"], render.risk_colour(c["risk"]), c["description"]]
            for c in data.get("capabilities", [])]
    lines.append(render.table(rows, headers=["CAPABILITY", "RISK", "WHAT IT MEANS"]))
    lines.append("")
    lines.append(render.wrap(
        "CRITICAL actions always ask, in every mode. There is no setting that "
        "turns that off.", indent=2))
    return "\n".join(lines)


def _render_ask(data: Dict[str, Any]) -> str:
    lines = [data.get("text", "").rstrip()]
    if data.get("tools_used"):
        lines.append("")
        lines.append(render.paint("  (used: %s)" % ", ".join(data["tools_used"]), "dim"))
    return "\n".join(lines)


def _render_plan(data: Dict[str, Any]) -> str:
    if not data.get("ok"):
        error = data.get("error") or {}
        lines = [render.paint("Could not make a plan.", "red"),
                 render.wrap(error.get("what", ""), indent=2),
                 render.wrap(error.get("why", ""), indent=2)]
        if error.get("fix"):
            lines.append(render.wrap(error["fix"], indent=2))
        if data.get("model_said"):
            lines.append("")
            lines.append(render.paint("  The model replied:", "dim"))
            lines.append(render.wrap(data["model_said"][:600], indent=4))
        return "\n".join(lines)

    lines = [render.heading("Plan %s" % data.get("plan_id", "")), "",
             render.wrap("Goal: " + data.get("goal", ""), indent=2), ""]
    lines.append(render.heading("  Options considered"))
    rows = []
    for option in data.get("ranked", []):
        rows.append([
            option["title"][:38],
            "%.2f" % option["expected_utility"],
            "%.0f%%" % (100 * option["probability"]),
            "%.2f" % option["risk"],
            "ruled out: " + ", ".join(option["ruled_out_by"])
            if not option["feasible"] else "",
        ])
    lines.append(render.table(rows, headers=["STRATEGY", "VALUE", "CONF", "RISK", ""]))
    lines.append("")
    lines.append(render.wrap(data.get("explanation", ""), indent=2))
    steps = data.get("steps") or []
    if steps:
        lines.append("")
        lines.append(render.heading("  Steps"))
        for index, step in enumerate(steps, 1):
            lines.append("    %d. %s" % (index, step["title"]))
    lines.append("")
    lines.append(render.paint(
        "  Nothing has been done yet. This is a proposal.", "dim"))
    return "\n".join(lines)


def _render_approvals(data: Dict[str, Any]) -> str:
    pending = data.get("pending") or []
    if not pending:
        return "Nothing is waiting for your approval."
    lines = [render.heading("Waiting for you"), ""]
    for item in pending:
        lines.append("  %s  [%s]" % (render.paint(item["id"], "bold"),
                                     render.risk_colour(item["risk"])))
        lines.append(render.wrap(item["summary"], indent=4))
        if item.get("resources"):
            lines.append(render.paint(render.wrap(
                "affects: " + ", ".join(item["resources"][:4]), indent=4), "dim"))
        lines.append("      approve: assistant approve %s      "
                     "refuse: assistant deny %s" % (item["id"], item["id"]))
        lines.append("")
    return "\n".join(lines)


def _render_memory(data: Dict[str, Any]) -> str:
    memories = data.get("memories") or []
    stats = data.get("stats") or {}
    lines = []
    if stats and not stats.get("enabled", True):
        lines.append(render.paint("Memory is switched OFF.", "yellow"))
        lines.append("")
    if not memories:
        lines.append("Nothing is remembered yet.")
        return "\n".join(lines)
    rows = [[m["id"], m["kind"],
             "%.2f" % m.get("effective_confidence", m.get("confidence", 0)),
             "pinned" if m.get("pinned") else "",
             m["text"][:60]] for m in memories]
    lines.append(render.table(rows, headers=["ID", "KIND", "CONF", "", "MEMORY"]))
    lines.append("")
    lines.append(render.paint(
        "  Remove one with 'assistant memory forget <id>'.", "dim"))
    return "\n".join(lines)


def _render_search(data: Dict[str, Any]) -> str:
    results = data.get("results") or []
    if not results:
        lines = ["No matches for %r." % data.get("query", "")]
        if data.get("hint"):
            lines.append(render.wrap(data["hint"], indent=2))
        return "\n".join(lines)
    lines = [render.heading("%d result(s) for %r" % (len(results), data.get("query"))), ""]
    for row in results:
        lines.append("  %s" % render.paint(row["name"], "bold"))
        lines.append(render.paint("    %s" % row["path"], "dim"))
        if row.get("excerpt"):
            lines.append(render.wrap(row["excerpt"].replace("\n", " "), indent=4))
        lines.append("")
    if data.get("note"):
        lines.append(render.paint(render.wrap(data["note"], indent=2), "dim"))
    return "\n".join(lines)


def _render_index_report(data: Dict[str, Any]) -> str:
    lines = [render.heading("Index updated"), "",
             "  scanned %s   added %s   updated %s   unchanged %s"
             % (data.get("scanned", 0), data.get("added", 0),
                data.get("updated", 0), data.get("unchanged", 0)),
             "  skipped %s   unreadable %s   removed %s   in %ss"
             % (data.get("skipped", 0), data.get("failed", 0),
                data.get("removed", 0), data.get("seconds", 0))]
    errors = data.get("errors") or []
    if errors:
        lines.append("")
        lines.append(render.paint("  Files that could not be read:", "yellow"))
        for message in errors[:8]:
            lines.append(render.wrap(message, indent=4))
    return "\n".join(lines)


def _render_duplicates(data: Dict[str, Any]) -> str:
    groups = data.get("groups") or []
    if not groups:
        return "No duplicate files found."
    lines = [render.heading("%d group(s) of identical files - %s could be freed"
                            % (len(groups), render.human_bytes(
                                data.get("reclaimable_bytes", 0)))), ""]
    for group in groups[:25]:
        lines.append("  %d copies, %s each" % (group["copies"],
                                               render.human_bytes(group["size_each"])))
        lines.append(render.paint("    keep:      %s" % group["keep"], "green"))
        for path in group["duplicates"][:6]:
            lines.append("    duplicate: %s" % path)
        lines.append("")
    lines.append(render.paint("  Nothing has been deleted.", "dim"))
    return "\n".join(lines)


def _render_logs(data: Dict[str, Any]) -> str:
    entries = data.get("entries") or []
    if not entries:
        return "No activity recorded yet."
    rows = []
    for entry in entries:
        rows.append([entry["ts"][11:19], entry["actor"], entry["event"],
                     entry.get("tool") or "", entry.get("decision") or "",
                     (entry.get("outcome") or entry.get("error") or "")[:34]])
    return render.table(rows, headers=["TIME", "WHO", "EVENT", "TOOL",
                                       "DECISION", "RESULT"])


def _render_tools(data: Dict[str, Any]) -> str:
    rows = [[t["name"], render.risk_colour(t["risk"]),
             "macOS" if t["macos_only"] else "any", t["description"][:48]]
            for t in data.get("tools", [])]
    return render.table(rows, headers=["TOOL", "RISK", "PLATFORM", "WHAT IT DOES"])


def _render_macos_permissions(data: Dict[str, Any]) -> str:
    if data.get("platform") != "macOS":
        return render.wrap(data.get("note", ""), indent=2)
    lines = [render.heading("macOS app permissions"), ""]
    for item in data.get("permissions", []):
        mark = render.paint("granted", "green") if item["granted"] \
            else render.paint(item["status"], "yellow")
        lines.append("  %-16s %s" % (item["app"], mark))
        lines.append(render.paint("      %s" % item["why_needed"], "dim"))
        if not item["granted"] and item.get("how_to_grant"):
            lines.append(render.wrap(item["how_to_grant"], indent=6))
        lines.append("")
    lines.append(render.rule())
    lines.append(render.wrap(data.get("full_disk_access", ""), indent=2))
    return "\n".join(lines)


# ==========================================================================
# the command tree
# ==========================================================================
EPILOG = """\
examples:
  assistant                                    just talk to it (easiest)
  assistant permissions grant ~/Documents      let it read your Documents
  assistant index build                        catalogue those files
  assistant ask "what am I missing on my science project?"
  assistant plan "help me organise my semester"
  assistant approvals                          see what is waiting for you
  assistant stop                               emergency stop, immediately

Most commands work whether or not the background service is running.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assistant",
        description="Personal AI OS - a permissioned AI layer for your Mac.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true",
                        help="Print raw JSON instead of formatted text.")
    parser.add_argument("--local", action="store_true",
                        help="Do the work in this process, ignoring the daemon.")
    parser.add_argument("--version", action="version",
                        version="Personal AI OS %s" % __version__)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("chat", help="Talk to it. No quotes, no command names. (default)")

    ask = sub.add_parser("ask", help="Ask a question or request something.")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--role", default="reasoning",
                     choices=["reasoning", "planning", "fast", "coding", "vision", "local"])
    ask.add_argument("--session", help="Continue an existing conversation.")

    plan = sub.add_parser("plan", help="Compare strategies for a goal.")
    plan_sub = plan.add_subparsers(dest="plan_command", metavar="<subcommand>")
    plan.add_argument("goal", nargs="*", help="The goal to plan for.")
    plan.add_argument("--risk", type=float, help="Risk ceiling, 0.0 to 1.0.")
    plan_sub.add_parser("list", help="Show recent plans.")
    plan_show = plan_sub.add_parser("show", help="Show one plan in full.")
    plan_show.add_argument("id")
    plan_cancel = plan_sub.add_parser("cancel", help="Cancel a plan.")
    plan_cancel.add_argument("id")

    sub.add_parser("status", help="What the assistant can do and has been doing.")

    doctor = sub.add_parser("doctor", help="Check the installation and explain problems.")
    doctor.add_argument("--request-permissions", action="store_true",
                        help="Ask macOS for app permissions now (run this from Terminal).")

    perms = sub.add_parser("permissions", help="See and change what it may do.")
    perms_sub = perms.add_subparsers(dest="permissions_command", metavar="<subcommand>")
    grant = perms_sub.add_parser("grant", help="Allow access to a folder.")
    grant.add_argument("path")
    grant.add_argument("--mode", default="read", choices=["read", "readwrite"])
    grant.add_argument("--note", default="")
    revoke = perms_sub.add_parser("revoke", help="Withdraw access to a folder.")
    revoke.add_argument("path")
    perms_sub.add_parser("capabilities", help="List every possible action and its risk.")
    perms_sub.add_parser("macos", help="Check macOS app permissions.")
    rule = perms_sub.add_parser("rule", help="Add or remove a standing rule.")
    rule_sub = rule.add_subparsers(dest="rule_command", metavar="<subcommand>")
    rule_add = rule_sub.add_parser("add", help="Add a rule.")
    rule_add.add_argument("--effect", required=True, choices=["allow", "deny", "ask"])
    rule_add.add_argument("--capability", required=True,
                          help="e.g. files.delete, or files.* for all file actions")
    rule_add.add_argument("--path", help="Optional path or glob it applies to.")
    rule_add.add_argument("--note", default="")
    rule_remove = rule_sub.add_parser("remove", help="Remove a rule by id.")
    rule_remove.add_argument("id", type=int)
    rule_sub.add_parser("list", help="List your rules.")

    sub.add_parser("approvals", help="Show actions waiting for your yes or no.")
    approve = sub.add_parser("approve", help="Approve a waiting action.")
    approve.add_argument("id")
    deny = sub.add_parser("deny", help="Refuse a waiting action.")
    deny.add_argument("id")

    stop = sub.add_parser("stop", help="EMERGENCY STOP - halt all activity now.")
    stop.add_argument("--reason", default="user requested stop")
    sub.add_parser("resume", help="Clear the emergency stop.")

    memory = sub.add_parser("memory", help="See and control what it remembers.")
    memory_sub = memory.add_subparsers(dest="memory_command", metavar="<subcommand>")
    memory_list = memory_sub.add_parser("list", help="Show everything remembered.")
    memory_list.add_argument("--kind")
    memory_list.add_argument("--limit", type=int, default=100)
    memory_search = memory_sub.add_parser("search", help="Search memories.")
    memory_search.add_argument("query", nargs="+")
    memory_add = memory_sub.add_parser("add", help="Remember something.")
    memory_add.add_argument("text", nargs="+")
    memory_add.add_argument("--kind", default="fact")
    memory_add.add_argument("--pin", action="store_true",
                            help="Never let this fade with age.")
    memory_forget = memory_sub.add_parser("forget", help="Remove a memory.")
    memory_forget.add_argument("id", nargs="?", type=int)
    memory_forget.add_argument("--all", action="store_true")
    memory_forget.add_argument("--yes", action="store_true",
                               help="Confirm erasing everything.")
    memory_sub.add_parser("export", help="Print all memories as JSON.")
    memory_sub.add_parser("stats", help="Summary counts.")

    index = sub.add_parser("index", help="Build and search the file catalogue.")
    index_sub = index.add_subparsers(dest="index_command", metavar="<subcommand>")
    index_build = index_sub.add_parser("build", help="Scan granted folders.")
    index_build.add_argument("--folder")
    index_build.add_argument("--full", action="store_true",
                             help="Re-read everything, not just changed files.")
    index_sub.add_parser("status", help="What is in the catalogue.")
    index_search = index_sub.add_parser("search", help="Search your files.")
    index_search.add_argument("query", nargs="+")
    index_search.add_argument("--limit", type=int, default=20)
    index_related = index_sub.add_parser("related", help="Find similar documents.")
    index_related.add_argument("path")
    index_sub.add_parser("clear", help="Empty the catalogue (not your files).")

    duplicates = sub.add_parser("duplicates", help="Find identical files.")
    duplicates.add_argument("--min-size", type=int, default=1024)

    config = sub.add_parser("config", help="See and change settings.")
    config_sub = config.add_subparsers(dest="config_command", metavar="<subcommand>")
    config_show = config_sub.add_parser("show", help="Print settings.")
    config_show.add_argument("section", nargs="?")
    config_set = config_sub.add_parser("set", help="Change one setting.")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_sub.add_parser("path", help="Where the settings file is.")

    logs = sub.add_parser("logs", help="What the assistant has done.")
    logs.add_argument("--limit", type=int, default=40)
    logs.add_argument("--event", help="Filter, e.g. 'tool.*' or 'policy.decision'.")

    sub.add_parser("tools", help="Every tool the assistant can use.")

    daemon = sub.add_parser("daemon", help="Manage the background service.")
    daemon_sub = daemon.add_subparsers(dest="daemon_command", metavar="<subcommand>")
    daemon_sub.add_parser("start", help="Start it in the background.")
    daemon_sub.add_parser("stop", help="Stop it.")
    daemon_sub.add_parser("restart", help="Restart it.")
    daemon_sub.add_parser("status", help="Is it running?")
    daemon_sub.add_parser("install", help="Make it start when you log in (macOS).")
    daemon_sub.add_parser("uninstall", help="Stop it starting at login.")
    daemon_sub.add_parser("serve", help="Run it in the foreground (for debugging).")
    daemon_sub.add_parser("tick", help="Run any due scheduled jobs once.")

    return parser


# ==========================================================================
# command implementations
# ==========================================================================
def _cmd_daemon(args: argparse.Namespace) -> int:
    from ..daemon import scheduler, server
    from ..macos import launchd

    action = args.daemon_command or "status"

    if action == "serve":
        server.serve()
        return 0

    if action == "start":
        if server.is_running():
            print("Already running.")
            return 0
        if paths.is_macos():
            result = launchd.install()
            print("Installed and started." if result["installed"]
                  else "Could not start it:\n  %s\n  %s"
                  % (result["error"], result["hint"]))
            return 0 if result["installed"] else 1
        import subprocess
        subprocess.Popen([sys.executable, "-m", "assistant.daemon", "--serve"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Started in the background.")
        return 0

    if action == "stop":
        if server.is_running():
            try:
                server.Client(timeout=10).call("shutdown")
            except AssistantError:
                pass
        if paths.is_macos():
            launchd.uninstall(remove_file=False)
        print("Stopped.")
        return 0

    if action == "restart":
        if paths.is_macos():
            print("Restarted." if launchd.restart().get("restarted")
                  else "Could not restart - try 'assistant daemon start'.")
            return 0
        _cmd_daemon(argparse.Namespace(daemon_command="stop"))
        return _cmd_daemon(argparse.Namespace(daemon_command="start"))

    if action == "install":
        result = launchd.install()
        print(render.as_json(result) if args.json else
              ("Installed. It will start automatically when you log in."
               if result["installed"] else
               "Could not install:\n  %s\n  %s" % (result["error"], result["hint"])))
        return 0 if result["installed"] else 1

    if action == "uninstall":
        print(render.as_json(launchd.uninstall()) if args.json
              else "Removed from login items. Your data is untouched.")
        return 0

    if action == "tick":
        for result in scheduler.Scheduler().tick_once():
            print(render.as_json(result) if args.json else result)
        return 0

    running = server.is_running()
    state = launchd.status()
    if args.json:
        print(render.as_json({"running": running, "launch_agent": state}))
    else:
        print("Background service: %s" % ("running" if running else "not running"))
        print("Socket:            %s" % paths.socket_path())
        if state.get("plist_exists"):
            print("Starts at login:   yes (%s)" % state["plist"])
        else:
            print("Starts at login:   no  ->  assistant daemon install")
    return 0


def _cmd_memory(args: argparse.Namespace) -> int:
    action = args.memory_command or "list"
    if action == "list":
        return _output(_call("memory_list", {"kind": args.kind, "limit": args.limit},
                             args.local), args.json, _render_memory)
    if action == "search":
        return _output(_call("memory_search", {"query": " ".join(args.query)},
                             args.local), args.json, _render_memory)
    if action == "add":
        data = _call("memory_add", {"text": " ".join(args.text), "kind": args.kind,
                                    "pinned": args.pin}, args.local)
        return _output(data, args.json,
                       lambda d: "Remembered (#%s): %s" % (d.get("id"), d.get("text")))
    if action == "forget":
        if args.all:
            data = _call("memory_forget", {"all": True, "confirm": args.yes}, args.local)
            return _output(data, args.json,
                           lambda d: "Forgot %s memories." % d["forgotten"])
        if args.id is None:
            print("Which one? Use 'assistant memory list' to see the ids, then "
                  "'assistant memory forget <id>'.")
            return 2
        data = _call("memory_forget", {"id": args.id}, args.local)
        return _output(data, args.json,
                       lambda d: "Forgotten." if d["forgotten"] else "No such memory.")
    if action == "export":
        return _output(_call("memory_export", {}, args.local), True)
    return _output(_call("memory_list", {"limit": 1}, args.local).get("stats", {}),
                   args.json, lambda d: render.as_json(d))


def _cmd_index(args: argparse.Namespace) -> int:
    action = args.index_command or "status"
    if action == "build":
        print("Scanning... (this can take a minute the first time)")
        return _output(_call("index_build", {"folder": args.folder, "full": args.full},
                             args.local), args.json, _render_index_report)
    if action == "search":
        return _output(_call("index_search", {"query": " ".join(args.query),
                                              "limit": args.limit}, args.local),
                       args.json, _render_search)
    if action == "related":
        return _output(_call("index_related", {"path": args.path}, args.local),
                       args.json, lambda d: render.as_json(d))
    if action == "clear":
        return _output(_call("index_clear", {}, args.local), args.json,
                       lambda d: "Removed %s entries from the catalogue. Your "
                                 "files are untouched." % d["removed"])
    return _output(_call("index_status", {}, args.local), args.json,
                   lambda d: render.as_json(d))


def _cmd_permissions(args: argparse.Namespace) -> int:
    action = args.permissions_command
    if action == "grant":
        data = _call("grant", {"path": args.path, "mode": args.mode,
                               "note": args.note}, args.local)
        return _output(data, args.json,
                       lambda d: "Granted %s access to %s" % (d["mode"], d["path"]))
    if action == "revoke":
        return _output(_call("revoke", {"path": args.path}, args.local), args.json,
                       lambda d: "Revoked." if d["revoked"] else "That folder was not granted.")
    if action == "capabilities":
        return _output(_call("permissions", {}, args.local), args.json,
                       _render_capabilities)
    if action == "macos":
        return _output(_call("macos_permissions", {}, args.local), args.json,
                       _render_macos_permissions)
    if action == "rule":
        rule_action = args.rule_command or "list"
        if rule_action == "add":
            data = _call("rule_add", {"effect": args.effect,
                                      "capability": args.capability,
                                      "path": args.path, "note": args.note}, args.local)
            return _output(data, args.json, lambda d: "Rule #%s added." % d["id"])
        if rule_action == "remove":
            return _output(_call("rule_remove", {"id": args.id}, args.local), args.json,
                           lambda d: "Removed." if d["removed"] else "No such rule.")
        return _output(_call("permissions", {}, args.local), args.json,
                       _render_permissions)
    return _output(_call("permissions", {}, args.local), args.json, _render_permissions)


def _cmd_plan(args: argparse.Namespace) -> int:
    action = getattr(args, "plan_command", None)
    if action == "list":
        return _output(_call("plan_list", {}, args.local), args.json,
                       lambda d: render.table(
                           [[p["id"], p["created_at"][:16], p["status"],
                             p["goal"][:44]] for p in d["plans"]],
                           headers=["ID", "CREATED", "STATUS", "GOAL"]))
    if action == "show":
        from ..planner import plan as plan_module
        return _output(_call("plan_show", {"id": args.id}, args.local), args.json,
                       plan_module.render)
    if action == "cancel":
        return _output(_call("plan_cancel", {"id": args.id}, args.local), args.json,
                       lambda d: "Cancelled." if d["cancelled"] else "Nothing to cancel.")
    goal = " ".join(args.goal or [])
    if not goal:
        print("What should I plan for?  e.g.  assistant plan \"organise my semester\"")
        return 2
    payload = {"goal": goal}
    if args.risk is not None:
        payload["risk_ceiling"] = args.risk
    return _output(_call("plan", payload, args.local), args.json, _render_plan)


def _cmd_config(args: argparse.Namespace) -> int:
    action = args.config_command or "show"
    if action == "set":
        value: Any = args.value
        lowered = str(value).lower()
        if lowered in ("true", "false"):
            value = lowered == "true"
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        return _output(_call("config_set", {"key": args.key, "value": value}, args.local),
                       args.json, lambda d: "%s = %s" % (d["key"], d["value"]))
    if action == "path":
        print(paths.config_path())
        return 0
    return _output(_call("config_show", {"section": args.section}, args.local),
                   args.json, lambda d: render.as_json(d["config"]))


# ==========================================================================
# entry point
# ==========================================================================
def main(argv: Optional[List[str]] = None) -> int:
    from ..logging_setup import setup
    setup(level="INFO")

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        # Running `assistant` on its own should start a conversation, not print
        # a wall of help. When output is piped somewhere (a script, a file) an
        # interactive prompt would hang forever, so fall back to help there.
        if sys.stdin.isatty() and sys.stdout.isatty():
            from . import chat
            return chat.run(lambda c, a=None: _call(c, a, args.local)) or 0
        parser.print_help()
        return 0

    try:
        command = args.command

        if command == "chat":
            from . import chat
            return chat.run(lambda c, a=None: _call(c, a, args.local)) or 0

        if command == "ask":
            payload = {"question": " ".join(args.question), "role": args.role}
            if args.session:
                payload["session_id"] = args.session
            return _output(_call("ask", payload, args.local), args.json, _render_ask)

        if command == "plan":
            return _cmd_plan(args)
        if command == "status":
            return _output(_call("status", {}, args.local), args.json, _render_status)
        if command == "doctor":
            return _output(_call("doctor",
                                 {"request_permissions": args.request_permissions},
                                 args.local), args.json, _render_doctor)
        if command == "permissions":
            return _cmd_permissions(args)
        if command == "approvals":
            return _output(_call("approvals", {}, args.local), args.json,
                           _render_approvals)
        if command == "approve":
            return _output(_call("approve", {"id": args.id}, args.local), args.json,
                           lambda d: "Approved. Ask again and it will go ahead.")
        if command == "deny":
            return _output(_call("deny", {"id": args.id}, args.local), args.json,
                           lambda d: "Refused. It will not happen.")
        if command == "stop":
            return _output(_call("stop", {"reason": args.reason}, args.local), args.json,
                           lambda d: render.paint(
                               "STOPPED. All autonomous activity has halted.\n"
                               "Type 'assistant resume' when you want it back.", "red"))
        if command == "resume":
            return _output(_call("resume", {}, args.local), args.json,
                           lambda d: "Resumed." if d["resumed"]
                           else "It was not stopped.")
        if command == "memory":
            return _cmd_memory(args)
        if command == "index":
            return _cmd_index(args)
        if command == "duplicates":
            return _output(_call("duplicates", {"min_size": args.min_size}, args.local),
                           args.json, _render_duplicates)
        if command == "config":
            return _cmd_config(args)
        if command == "logs":
            return _output(_call("logs", {"limit": args.limit, "event": args.event},
                                 args.local), args.json, _render_logs)
        if command == "tools":
            return _output(_call("tools", {}, args.local), args.json, _render_tools)
        if command == "daemon":
            return _cmd_daemon(args)

        parser.print_help()
        return 2

    except AssistantError as exc:
        print(render.error(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
