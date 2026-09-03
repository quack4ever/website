"""A tiny JSON-over-HTTP helper for the non-Anthropic providers.

WHY urllib AND NOT `requests`?
------------------------------
``urllib`` is part of Python itself, so it needs no installation on your Mac.
The Anthropic provider uses the official SDK (that is what Anthropic supports
and documents); these other providers speak plain, stable HTTP JSON, so the
standard library is enough and keeps the install a single step.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ..errors import ProviderError


def post_json(url: str, payload: Dict[str, Any],
              headers: Optional[Dict[str, str]] = None,
              timeout: float = 300.0, provider: str = "provider") -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            pass
        raise ProviderError(
            what="%s returned an error (HTTP %s)." % (provider, exc.code),
            why=detail or str(exc),
            tried="POST %s" % url,
            needs="A working endpoint and valid credentials."
                  if exc.code in (401, 403) else "A valid request.",
            fix=("Check your API key for this provider."
                 if exc.code in (401, 403) else
                 "Check that the base_url in your config is correct: %s" % url),
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(
            what="Could not reach %s." % provider,
            why=str(exc.reason),
            tried="Connecting to %s" % url,
            needs="The service to be running and reachable.",
            fix="If this is a local model, check it is started (for Ollama: "
                "run 'ollama serve'). If it is remote, check your internet.",
        ) from exc
    except TimeoutError as exc:
        raise ProviderError(
            what="%s did not answer in time." % provider,
            why="The request timed out after %.0f seconds." % timeout,
            tried="POST %s" % url,
            needs="A faster model or a longer timeout.",
            fix="Local models on older Macs can be slow - try a smaller model.",
        ) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            what="%s sent back something that was not JSON." % provider,
            why=raw[:300],
            tried="Parsing the response body",
            needs="A JSON response.",
            fix="Check that base_url points at an API endpoint, not a web page.",
        ) from exc


def get_json(url: str, timeout: float = 10.0, provider: str = "provider") -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise ProviderError(
            what="Could not reach %s." % provider,
            why=str(exc),
            tried="GET %s" % url,
            needs="The service to be running.",
            fix="Start the service and try again.",
        ) from exc
