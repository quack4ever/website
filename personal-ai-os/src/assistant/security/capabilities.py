"""The catalogue of things that can be requested, and how dangerous each is.

ANALOGY
-------
A capability is a *door* in the building.  ``files.read`` is the reading room;
``files.delete`` is the shredder room.  Each door has a colour-coded risk
label, and the security guard uses that label to decide whether it needs your
signature.

RISK TIERS
----------
LOW      - reading and looking.  Reversible, harmless.
MEDIUM   - creating and changing things you can undo.
HIGH     - deleting, running commands, writing to your calendar.
CRITICAL - irreversible or visible to other people.  ALWAYS asks. No exceptions.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

LOW = "low"
MEDIUM = "medium"
HIGH = "high"
CRITICAL = "critical"

#: Ordering used for "is this tier at or below that tier?" comparisons.
TIER_ORDER: Dict[str, int] = {LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4}


class Capability(NamedTuple):
    name: str
    risk: str
    description: str
    #: True when the capability writes to the filesystem (needs a 'readwrite' scope).
    writes: bool = False
    #: True when the effect cannot be undone by the assistant itself.
    irreversible: bool = False
    #: True when the effect is visible outside your Mac (messages, mail, network).
    outward: bool = False


_CAPS: List[Capability] = [
    # ---- looking around ------------------------------------------------
    Capability("files.search", LOW, "Find files by name or content inside your granted folders"),
    Capability("files.read", LOW, "Read the contents of a file inside your granted folders"),
    Capability("files.stat", LOW, "Look at a file's size and dates without reading it"),
    Capability("files.list", LOW, "List what is inside a folder"),
    Capability("index.query", LOW, "Search the local catalogue of your files"),
    Capability("index.build", MEDIUM, "Scan your granted folders and build the catalogue"),
    Capability("memory.read", LOW, "Recall what it remembers about you"),
    Capability("system.info", LOW, "Read basic system information (disk space, OS version)"),
    Capability("clipboard.read", MEDIUM, "Read what is currently on your clipboard"),

    # ---- changing your files -------------------------------------------
    Capability("files.create", MEDIUM, "Create a new file", writes=True),
    Capability("files.write", MEDIUM, "Change the contents of an existing file", writes=True),
    Capability("files.copy", MEDIUM, "Copy a file", writes=True),
    Capability("files.move", MEDIUM, "Move or rename a file", writes=True),
    Capability("files.mkdir", MEDIUM, "Create a folder", writes=True),
    Capability("memory.write", MEDIUM, "Remember something about you long-term"),
    Capability("clipboard.write", MEDIUM, "Put something on your clipboard"),

    # ---- higher stakes ---------------------------------------------------
    Capability("files.trash", HIGH, "Move a file to the Trash (recoverable)", writes=True),
    Capability("files.delete", HIGH, "Permanently delete a file", writes=True, irreversible=True),
    Capability("shell.execute", HIGH, "Run an allow-listed command in the Terminal"),
    Capability("app.launch", HIGH, "Open an application"),
    Capability("calendar.read", MEDIUM, "Read your calendar"),
    Capability("calendar.write", HIGH, "Create or change a calendar event"),
    Capability("reminders.read", MEDIUM, "Read your reminders"),
    Capability("reminders.write", HIGH, "Create a reminder"),
    Capability("notify.send", MEDIUM, "Show you a macOS notification"),
    Capability("contacts.read", HIGH, "Read your contacts"),
    Capability("net.fetch", HIGH, "Fetch a web page", outward=True),

    # ---- always asks, no matter what ------------------------------------
    Capability("files.bulk_delete", CRITICAL, "Delete many files at once",
               writes=True, irreversible=True),
    Capability("message.send", CRITICAL, "Send a message to another person",
               outward=True, irreversible=True),
    Capability("mail.send", CRITICAL, "Send an email", outward=True, irreversible=True),
    Capability("security.change", CRITICAL, "Change a security or system setting",
               irreversible=True),
    Capability("purchase.make", CRITICAL, "Spend money", outward=True, irreversible=True),
    Capability("publish.content", CRITICAL, "Publish something publicly",
               outward=True, irreversible=True),
]

REGISTRY: Dict[str, Capability] = {c.name: c for c in _CAPS}


def get(name: str) -> Optional[Capability]:
    return REGISTRY.get(name)


def risk_of(name: str) -> str:
    """Unknown capabilities are treated as CRITICAL.

    This is 'fail closed': if some future code asks for a door we have never
    heard of, we do not wave it through - we make it ask you.
    """
    cap = REGISTRY.get(name)
    return cap.risk if cap else CRITICAL


def tier_at_most(tier: str, ceiling: str) -> bool:
    """True when `tier` is no more dangerous than `ceiling`."""
    return TIER_ORDER.get(tier, 4) <= TIER_ORDER.get(ceiling, 0)


def matches(pattern: str, name: str) -> bool:
    """Match a capability name against a rule pattern.

    ``files.*``  matches every files capability.
    ``*``        matches everything.
    ``files.read`` matches only itself.
    """
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return name.startswith(pattern[:-1])
    return pattern == name


def all_capabilities() -> List[Capability]:
    return sorted(_CAPS, key=lambda c: (TIER_ORDER[c.risk], c.name))
