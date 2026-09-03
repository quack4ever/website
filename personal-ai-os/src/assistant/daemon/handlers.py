"""What the daemon can actually be asked to do.

WHY THIS IS A SEPARATE FILE
---------------------------
The same operations are needed in two places: over the socket (when the daemon
is running) and directly in-process (when it is not, so the CLI still works).
Writing them once here means the two paths can never drift apart and behave
differently - which would be a horrible class of bug to debug.

Every handler returns plain data. None of them print anything: rendering is the
CLI's job, so the same handler serves a terminal, a future menu-bar app, or
anything else.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .. import audit, config as config_module, db, paths, tools
from ..ai import Router
from ..errors import AssistantError
from ..index import dedupe, indexer, search as index_search
from ..logging_setup import get
from ..macos import launchd, tcc
from ..memory import store as memory_store
from ..orchestrator import Agent
from ..orchestrator import reflect as reflect_module
from ..planner import plan as plan_module
from ..security import capabilities, consent, killswitch, pathguard, policy
from ..version import __version__

log = get(__name__)

Handler = Callable[[Dict[str, Any]], Any]
_HANDLERS: Dict[str, Handler] = {}


def handler(name: str) -> Callable[[Handler], Handler]:
    def register(function: Handler) -> Handler:
        _HANDLERS[name] = function
        return function
    return register


def dispatch(command: str, args: Optional[Dict[str, Any]] = None) -> Any:
    function = _HANDLERS.get(command)
    if function is None:
        raise AssistantError(
            what="Unknown command '%s'." % command,
            why="The daemon does not know how to do that.",
            tried="Looking up '%s'" % command,
            needs="A valid command.",
            fix="Known commands: %s" % ", ".join(sorted(_HANDLERS)),
        )
    return function(args or {})


def commands() -> list:
    return sorted(_HANDLERS)


# --------------------------------------------------------------------------
# status and health
# --------------------------------------------------------------------------
@handler("ping")
def _ping(args: Dict[str, Any]) -> Any:
    return {"pong": True, "version": __version__}


@handler("status")
def _status(args: Dict[str, Any]) -> Any:
    cfg = config_module.load()
    engine = policy.PolicyEngine(cfg)
    return {
        "version": __version__,
        "home": str(paths.home()),
        "autonomy_mode": cfg.autonomy_mode,
        "auto_approves_up_to": engine.ceiling() or "nothing",
        "kill_switch": killswitch.status(),
        "scopes": [{"path": str(s.path), "mode": s.mode}
                   for s in pathguard.list_scopes()],
        "rules": len(policy.list_rules()),
        "pending_approvals": len(consent.pending()),
        "memory": memory_store.stats(),
        "index": indexer.status(),
        "models": Router(cfg).status(),
        "activity": audit.summary(days=7),
        "daemon": {"running": True},
        "launch_agent": launchd.status(),
    }


@handler("doctor")
def _doctor(args: Dict[str, Any]) -> Any:
    from ..diagnostics import doctor
    return doctor.run(request_permissions=bool(args.get("request_permissions")))


# --------------------------------------------------------------------------
# conversation
# --------------------------------------------------------------------------
@handler("ask")
def _ask(args: Dict[str, Any]) -> Any:
    agent = Agent(session_id=args.get("session_id"))
    result = agent.ask(str(args.get("question", "")),
                       role=str(args.get("role", "reasoning")),
                       max_iterations=args.get("max_iterations"),
                       approvals=args.get("approvals"))
    return result.to_dict()


@handler("plan")
def _plan(args: Dict[str, Any]) -> Any:
    agent = Agent(session_id=args.get("session_id"))
    return agent.plan(str(args.get("goal", "")), risk_ceiling=args.get("risk_ceiling"))


@handler("plan_show")
def _plan_show(args: Dict[str, Any]) -> Any:
    found = plan_module.get(str(args.get("id", "")))
    if not found:
        raise AssistantError(
            what="No plan with id %r." % args.get("id"),
            why="It may have been deleted, or the id is mistyped.",
            needs="A valid plan id.",
            fix="Run 'assistant plan list' to see them.")
    return found


@handler("plan_list")
def _plan_list(args: Dict[str, Any]) -> Any:
    return {"plans": plan_module.list_plans(limit=int(args.get("limit", 20)))}


@handler("plan_cancel")
def _plan_cancel(args: Dict[str, Any]) -> Any:
    return {"cancelled": plan_module.cancel(str(args.get("id", "")))}


@handler("reflect")
def _reflect(args: Dict[str, Any]) -> Any:
    return reflect_module.reflect(str(args.get("goal", "")),
                                  str(args.get("what_happened", "")),
                                  session_id=args.get("session_id"),
                                  plan_id=args.get("plan_id"))


# --------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------
@handler("permissions")
def _permissions(args: Dict[str, Any]) -> Any:
    engine = policy.PolicyEngine()
    state = engine.explain()
    state["capabilities"] = [c._asdict() for c in capabilities.all_capabilities()]
    state["forbidden"] = pathguard.describe()
    return state


@handler("grant")
def _grant(args: Dict[str, Any]) -> Any:
    scope = pathguard.grant(str(args["path"]), mode=str(args.get("mode", "read")),
                            note=str(args.get("note", "")))
    audit.record("policy.scope_granted", actor="user",
                 arguments={"path": str(scope.path), "mode": scope.mode})
    return {"path": str(scope.path), "mode": scope.mode, "granted": True}


@handler("revoke")
def _revoke(args: Dict[str, Any]) -> Any:
    removed = pathguard.revoke(str(args["path"]))
    audit.record("policy.scope_revoked", actor="user", arguments={"path": args["path"]})
    return {"revoked": removed}


@handler("rule_add")
def _rule_add(args: Dict[str, Any]) -> Any:
    rule_id = policy.add_rule(str(args["effect"]), str(args["capability"]),
                              args.get("path"), str(args.get("note", "")))
    return {"id": rule_id}


@handler("rule_remove")
def _rule_remove(args: Dict[str, Any]) -> Any:
    return {"removed": policy.remove_rule(int(args["id"]))}


# --------------------------------------------------------------------------
# approvals and the emergency stop
# --------------------------------------------------------------------------
@handler("approvals")
def _approvals(args: Dict[str, Any]) -> Any:
    return {"pending": consent.pending(limit=int(args.get("limit", 50)))}


@handler("approve")
def _approve(args: Dict[str, Any]) -> Any:
    item = consent.decide(str(args["id"]), approved=True, by="user",
                          remember=bool(args.get("remember")))
    return {"approved": True, "request": item}


@handler("deny")
def _deny(args: Dict[str, Any]) -> Any:
    item = consent.decide(str(args["id"]), approved=False, by="user")
    return {"denied": True, "request": item}


@handler("stop")
def _stop(args: Dict[str, Any]) -> Any:
    killswitch.engage(str(args.get("reason", "user requested stop")))
    audit.record("killswitch.engaged", actor="user",
                 arguments={"reason": args.get("reason")})
    return {"stopped": True, "status": killswitch.status()}


@handler("shutdown")
def _shutdown(args: Dict[str, Any]) -> Any:
    """Ask the daemon to stop. It replies first, then closes."""
    from .server import stop_serving
    audit.record("daemon.shutdown_requested", actor="user")
    return {"stopping": stop_serving()}


@handler("resume")
def _resume(args: Dict[str, Any]) -> Any:
    released = killswitch.release()
    audit.record("killswitch.released", actor="user")
    return {"resumed": released}


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------
@handler("memory_list")
def _memory_list(args: Dict[str, Any]) -> Any:
    return {"memories": memory_store.list_all(kind=args.get("kind"),
                                              limit=int(args.get("limit", 100))),
            "stats": memory_store.stats()}


@handler("memory_search")
def _memory_search(args: Dict[str, Any]) -> Any:
    return {"memories": memory_store.search(str(args.get("query", "")),
                                            limit=int(args.get("limit", 20)))}


@handler("memory_add")
def _memory_add(args: Dict[str, Any]) -> Any:
    return memory_store.remember(str(args["text"]), kind=str(args.get("kind", "fact")),
                                 pinned=bool(args.get("pinned")), source="cli")


@handler("memory_edit")
def _memory_edit(args: Dict[str, Any]) -> Any:
    return memory_store.edit(int(args["id"]), text=args.get("text"),
                             kind=args.get("kind"), confidence=args.get("confidence"),
                             pinned=args.get("pinned"))


@handler("memory_forget")
def _memory_forget(args: Dict[str, Any]) -> Any:
    if args.get("all"):
        return {"forgotten": memory_store.forget_all(confirm=bool(args.get("confirm")))}
    return {"forgotten": memory_store.forget(int(args["id"]))}


@handler("memory_export")
def _memory_export(args: Dict[str, Any]) -> Any:
    return {"memories": memory_store.export()}


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------
@handler("index_build")
def _index_build(args: Dict[str, Any]) -> Any:
    return indexer.index_scopes(only=args.get("folder"),
                                full=bool(args.get("full"))).to_dict()


@handler("index_status")
def _index_status(args: Dict[str, Any]) -> Any:
    return indexer.status()


@handler("index_search")
def _index_search(args: Dict[str, Any]) -> Any:
    return index_search.search(str(args.get("query", "")),
                               limit=int(args.get("limit", 20)),
                               kind=args.get("kind"))


@handler("index_related")
def _index_related(args: Dict[str, Any]) -> Any:
    return index_search.related(str(args["path"]), limit=int(args.get("limit", 10)))


@handler("index_clear")
def _index_clear(args: Dict[str, Any]) -> Any:
    return {"removed": indexer.clear()}


@handler("duplicates")
def _duplicates(args: Dict[str, Any]) -> Any:
    return dedupe.find_duplicates(min_size=int(args.get("min_size", 1024)),
                                  limit=int(args.get("limit", 100)))


# --------------------------------------------------------------------------
# config, logs, tools
# --------------------------------------------------------------------------
@handler("config_show")
def _config_show(args: Dict[str, Any]) -> Any:
    cfg = config_module.load()
    section = args.get("section")
    return {"config": cfg.get(section) if section else cfg.data,
            "path": str(cfg.path), "problems": cfg.validate()}


@handler("config_set")
def _config_set(args: Dict[str, Any]) -> Any:
    cfg = config_module.load()
    cfg.set(str(args["key"]), args["value"])
    problems = cfg.validate()
    if problems:
        raise AssistantError(
            what="That change would make the configuration invalid.",
            why="; ".join(problems),
            tried="Setting %s = %r" % (args["key"], args["value"]),
            needs="A valid value.",
            fix="Nothing was saved. Run 'assistant config show' to see the "
                "current settings.")
    cfg.save()
    audit.record("config.changed", actor="user",
                 arguments={"key": args["key"], "value": args["value"]})
    return {"key": args["key"], "value": cfg.get(str(args["key"]))}


@handler("logs")
def _logs(args: Dict[str, Any]) -> Any:
    return {"entries": audit.tail(limit=int(args.get("limit", 50)),
                                  event=args.get("event"),
                                  since=args.get("since"))}


@handler("tools")
def _tools(args: Dict[str, Any]) -> Any:
    tools.load_all()
    return {"tools": [
        {"name": s.name, "capability": s.capability, "risk": s.risk,
         "description": s.description, "macos_only": s.macos_only}
        for s in tools.list_specs()]}


@handler("macos_permissions")
def _macos_permissions(args: Dict[str, Any]) -> Any:
    report = tcc.check_all()
    report["full_disk_access"] = tcc.full_disk_access_note()
    return report
