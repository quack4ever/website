"""Turning data into readable terminal output.

Kept separate from the commands so the same handler can serve a terminal, a
script (with --json), or a future graphical app. No command builds strings
itself; they all come here.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

#: ANSI colour codes. Disabled automatically when output is piped to a file,
#: when NO_COLOR is set, or when the terminal says it cannot do colour.
_COLOURS = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
}


def colour_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def paint(text: str, *styles: str) -> str:
    if not colour_enabled():
        return text
    prefix = "".join(_COLOURS.get(s, "") for s in styles)
    return prefix + text + _COLOURS["reset"] if prefix else text


def width(default: int = 80) -> int:
    try:
        return max(40, min(120, shutil.get_terminal_size((default, 24)).columns))
    except Exception:
        return default


def heading(text: str) -> str:
    return paint(text, "bold", "cyan")


def rule(char: str = "-") -> str:
    return paint(char * width(), "dim")


def bullet(text: str, mark: str = "  •") -> str:
    return "%s %s" % (mark, text)


def status_mark(state: str) -> str:
    return {
        "ok": paint("  OK ", "green"),
        "warning": paint(" WARN", "yellow"),
        "problem": paint(" FAIL", "red"),
    }.get(state, "  ?  ")


def wrap(text: str, indent: int = 0, columns: Optional[int] = None) -> str:
    import textwrap
    columns = columns or width()
    prefix = " " * indent
    out: List[str] = []
    for paragraph in str(text).split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        out.extend(textwrap.wrap(paragraph, width=columns - indent,
                                 initial_indent=prefix, subsequent_indent=prefix)
                   or [prefix])
    return "\n".join(out)


def table(rows: Sequence[Sequence[Any]], headers: Optional[Sequence[str]] = None) -> str:
    """A plain aligned table. No box-drawing: it must stay copy-pasteable.

    When the rows are wider than the terminal we do NOT clip the line, because
    that silently drops the right-hand columns - and those are often the
    important ones (a scope's read/readwrite mode, for instance). Instead we
    shrink the widest column until everything fits, and elide inside that
    column. Long paths are elided in the MIDDLE, keeping the start and the
    filename, which are the parts you actually recognise.
    """
    if not rows:
        return paint("  (nothing to show)", "dim")

    body = [[_cell(c) for c in row] for row in rows]
    header_row = [str(h) for h in headers] if headers else None
    all_rows = ([header_row] if header_row else []) + body
    columns = max(len(r) for r in all_rows)
    for row in all_rows:
        while len(row) < columns:
            row.append("")

    widths = [max(len(r[i]) for r in all_rows) for i in range(columns)]
    gap = 2
    available = width() - 2 - gap * (columns - 1)

    # Shrink the widest column repeatedly until the whole row fits.
    guard = 0
    while sum(widths) > available and guard < 500:
        guard += 1
        widest = max(range(columns), key=lambda i: widths[i])
        if widths[widest] <= 8:
            break
        widths[widest] -= max(1, (sum(widths) - available) // 2 or 1)

    lines = []
    for position, row in enumerate(all_rows):
        cells = [_fit(row[i], widths[i]) for i in range(columns)]
        line = "  " + (" " * gap).join(
            cell.ljust(widths[i]) if i < columns - 1 else cell
            for i, cell in enumerate(cells)).rstrip()
        lines.append(paint(line, "bold") if header_row and position == 0 else line)
    return "\n".join(lines)


def _fit(text: str, limit: int) -> str:
    """Shorten `text` to `limit`, eliding the middle of path-like values."""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    if "/" in text and limit >= 12:
        keep_end = min(len(text), max(6, limit // 2))
        head = limit - keep_end - 1
        return text[:head] + "\u2026" + text[-keep_end:]
    return text[: limit - 1] + "\u2026"


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def as_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def error(exc: Any) -> str:
    """Render an AssistantError the way a helpful person would explain it."""
    if hasattr(exc, "what"):
        lines = [paint("Problem:  ", "red", "bold") + str(exc.what)]
        for label, value in (("Why:", getattr(exc, "why", "")),
                             ("Tried:", getattr(exc, "tried", "")),
                             ("Needs:", getattr(exc, "needs", ""))):
            if value:
                lines.append("%-9s %s" % (label, value))
        fix = getattr(exc, "fix", "")
        if fix:
            lines.append("")
            lines.append(paint("What you can do:", "green", "bold"))
            lines.append(wrap(fix, indent=2))
        return "\n".join(lines)
    return paint("Problem: ", "red", "bold") + str(exc)


def human_bytes(count: Any) -> str:
    try:
        value = float(count)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return "%.0f %s" % (value, unit) if unit == "B" else "%.1f %s" % (value, unit)
        value /= 1024
    return str(count)


def risk_colour(risk: str) -> str:
    return {"low": paint(risk, "green"), "medium": paint(risk, "yellow"),
            "high": paint(risk, "red"),
            "critical": paint(risk, "red", "bold")}.get(risk, risk)
