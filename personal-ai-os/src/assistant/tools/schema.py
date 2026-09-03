"""A small JSON-Schema checker.

WHY WRITE OUR OWN?
------------------
Tool arguments arrive from a language model, so they are *untrusted input*.
They must be validated before anything touches them.  The full `jsonschema`
library would be an extra dependency to install on every Mac; we only need a
well-defined subset, so we implement exactly that subset and nothing more.
Fewer moving parts, no supply-chain surface, and the error messages can be
written for a beginner.

SUPPORTED
---------
type (string/integer/number/boolean/array/object/null), required, properties,
enum, minimum, maximum, minLength, maxLength, pattern, items, default,
additionalProperties.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..errors import ValidationError

_TYPES: Dict[str, Tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "integer":
        # In Python `True` is an int.  A boolean is not an integer here.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    types = _TYPES.get(expected)
    return isinstance(value, types) if types else True


def _check(value: Any, spec: Dict[str, Any], where: str, problems: List[str]) -> Any:
    expected = spec.get("type")
    if expected and not _type_ok(value, expected):
        problems.append("%s should be a %s but got %s"
                        % (where, expected, type(value).__name__))
        return value

    if "enum" in spec and value not in spec["enum"]:
        problems.append("%s must be one of %s (got %r)"
                        % (where, ", ".join(map(repr, spec["enum"])), value))

    if expected == "string" and isinstance(value, str):
        if "minLength" in spec and len(value) < spec["minLength"]:
            problems.append("%s must be at least %d characters"
                            % (where, spec["minLength"]))
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            problems.append("%s must be at most %d characters"
                            % (where, spec["maxLength"]))
        if "pattern" in spec and not re.search(spec["pattern"], value):
            problems.append("%s does not match the required format" % where)

    if expected in ("number", "integer") and isinstance(value, (int, float)):
        if "minimum" in spec and value < spec["minimum"]:
            problems.append("%s must be at least %s" % (where, spec["minimum"]))
        if "maximum" in spec and value > spec["maximum"]:
            problems.append("%s must be at most %s" % (where, spec["maximum"]))

    if expected == "array" and isinstance(value, list):
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            problems.append("%s may contain at most %d items" % (where, spec["maxItems"]))
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for i, item in enumerate(value):
                _check(item, item_spec, "%s[%d]" % (where, i), problems)

    if expected == "object" and isinstance(value, dict):
        value = _check_object(value, spec, where, problems)

    return value


def _check_object(value: Dict[str, Any], spec: Dict[str, Any], where: str,
                  problems: List[str]) -> Dict[str, Any]:
    properties = spec.get("properties", {}) or {}
    required = spec.get("required", []) or []
    out: Dict[str, Any] = {}

    for key in required:
        if key not in value:
            problems.append("%s is missing the required field '%s'" % (where or "input", key))

    for key, item in value.items():
        if key in properties:
            out[key] = _check(item, properties[key], "%s.%s" % (where, key) if where else key,
                              problems)
        elif spec.get("additionalProperties", False) is False:
            problems.append("%s does not accept the field '%s' (allowed: %s)"
                            % (where or "input", key,
                               ", ".join(sorted(properties)) or "none"))
        else:
            out[key] = item

    # Fill in defaults for anything not supplied.
    for key, prop in properties.items():
        if key not in out and "default" in prop:
            out[key] = prop["default"]
    return out


def validate(arguments: Any, spec: Dict[str, Any], tool_name: str = "") -> Dict[str, Any]:
    """Check and normalise tool arguments.  Raises ValidationError if wrong.

    Returns a *new* dict containing only known fields plus defaults - so a
    handler can never be surprised by an extra key the model invented.
    """
    if not isinstance(arguments, dict):
        raise ValidationError(
            what="Tool arguments must be an object.",
            why="Got %s instead of a set of named fields." % type(arguments).__name__,
            tried="Validating arguments for '%s'" % tool_name,
            needs="A JSON object like {\"path\": \"...\"}",
            fix="This is an internal error - please report it with the log line.",
        )
    problems: List[str] = []
    cleaned = _check_object(arguments, spec, "", problems)
    if problems:
        raise ValidationError(
            what="The arguments for '%s' were not valid." % tool_name,
            why="; ".join(problems),
            tried="Checking the arguments against the tool's definition",
            needs="Arguments matching: %s" % ", ".join(sorted(spec.get("properties", {}))),
            fix="If you asked for this in plain English, rephrase and try "
                "again. If it keeps happening, run 'assistant logs' and report it.",
            details={"problems": problems},
        )
    return cleaned
