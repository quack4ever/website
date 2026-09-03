"""Finding and hiding secrets.

WHY THIS EXISTS
---------------
Two different jobs need the same skill:

1. The **audit log** must record what happened - but if an argument contained
   your API key, writing it to a log file would create a *new* security
   problem while trying to solve one.
2. The **memory system** must refuse to remember passwords, even if you
   accidentally paste one into a conversation.

So both call into here.  Think of it as a marker pen that automatically blacks
out anything that looks like a secret before the page is filed away.

IMPORTANT HONESTY NOTE
----------------------
Pattern matching cannot catch every possible secret.  It catches the common,
well-shaped ones (API keys, private keys, card numbers).  It is a safety net,
not a guarantee - which is why the real protection is that we never ask for
your secrets in the first place.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

MASK = "[REDACTED]"

# Each entry: (name, compiled pattern).  Patterns are deliberately specific so
# ordinary text is not mangled.
_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private_key_block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\b(?:bearer|authorization:\s*bearer)\s+[A-Za-z0-9._\-]{16,}")),
    ("password_assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)"
        r"\s*[:=]\s*[\"']?([^\s\"',;]{6,})[\"']?")),
    # People rarely write "password=x" in conversation.  They write
    # "my password is x".  Without this pattern the natural phrasing sailed
    # straight past the check and into long-term memory.
    ("password_phrase", re.compile(
        r"(?i)\b(?:password|passcode|passphrase|pin\s+number|"
        r"api[ _-]?key|secret[ _-]?key|access[ _-]?token|auth[ _-]?token)\b"
        r"\s*(?:is|was|are|:|=)\s+[\"']?([^\s\"',;]{6,})[\"']?")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("ssn_us", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

# Keys in a dict whose *value* is always masked regardless of shape.
_SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_token", "refresh_token", "authorization", "auth", "private_key",
    "client_secret", "session_key", "credentials",
}


#: If one of these follows "my password is", the sentence is *about* a
#: password rather than containing one ("my password is stored in 1Password").
#: Without this list the checker would refuse harmless notes.
_NON_SECRET_VALUES = frozenset({
    "stored", "saved", "secret", "safe", "hidden", "written", "kept",
    "changed", "expired", "wrong", "correct", "strong", "weak", "unknown",
    "somewhere", "different", "the", "my", "our", "your", "this", "that",
    "always", "never", "empty", "blank", "required", "needed", "reset",
})


def _is_real_value(text: str) -> bool:
    """Filter out sentences that mention a password without revealing one."""
    stripped = text.strip().strip("\"'.,;:")
    return bool(stripped) and stripped.lower() not in _NON_SECRET_VALUES

def _luhn_ok(digits: str) -> bool:
    """Credit-card checksum, so we do not black out every long number."""
    total, alt = 0, False
    for ch in reversed(digits):
        if not ch.isdigit():
            return False
        digit = int(ch)
        if alt:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        alt = not alt
    return total % 10 == 0 and len(digits) >= 13


def find_secrets(text: str) -> List[str]:
    """Return the names of secret kinds detected in `text` (no values)."""
    if not text:
        return []
    found: List[str] = []
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if name == "credit_card":
                digits = re.sub(r"[^0-9]", "", match.group(0))
                if not _luhn_ok(digits):
                    continue
            if name == "password_phrase" and not _is_real_value(match.group(1)):
                continue
            found.append(name)
            break
    return found


def redact_text(text: str) -> str:
    """Replace anything that looks like a secret with ``[REDACTED]``."""
    if not text:
        return text
    out = text
    for name, pattern in _PATTERNS:
        if name == "credit_card":
            def _cc(match: "re.Match[str]") -> str:
                digits = re.sub(r"[^0-9]", "", match.group(0))
                return MASK if _luhn_ok(digits) else match.group(0)
            out = pattern.sub(_cc, out)
        elif name in ("password_assignment", "password_phrase"):
            def _pw(match: "re.Match[str]") -> str:
                whole, value = match.group(0), match.group(1)
                if name == "password_phrase" and not _is_real_value(value):
                    return whole
                return whole.replace(value, MASK)
            out = pattern.sub(_pw, out)
        else:
            out = pattern.sub(MASK, out)
    return out


def redact_value(value: Any, _depth: int = 0) -> Any:
    """Recursively redact strings inside dicts/lists (for tool arguments)."""
    if _depth > 12:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                out[key] = MASK
            else:
                out[key] = redact_value(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(item, _depth + 1) for item in value]
    return value


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))
