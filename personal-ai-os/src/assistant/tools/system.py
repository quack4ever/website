"""Reading harmless facts about the computer.

Nothing here touches your files or your accounts.  It answers questions like
"how much disk space is left" and "what version of macOS is this", which the
planner genuinely needs (there is no point suggesting a 40 GB backup on a Mac
with 2 GB free).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any, Dict

from .. import paths
from .registry import ExecContext, ToolSpec, register


def _mac_product_version() -> Dict[str, str]:
    """`sw_vers` is the supported way to ask macOS what version it is."""
    out: Dict[str, str] = {}
    if not paths.is_macos():
        return out
    try:
        result = subprocess.run(["/usr/bin/sw_vers"], capture_output=True,
                                timeout=5, check=False)
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                out[key.strip()] = value.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def _info(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    usage = shutil.disk_usage(os.path.expanduser("~"))
    info: Dict[str, Any] = {
        "platform": platform.system(),
        "is_macos": paths.is_macos(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "home_disk": {
            "total_gb": round(usage.total / 1e9, 1),
            "used_gb": round(usage.used / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
            "percent_free": round(100.0 * usage.free / usage.total, 1) if usage.total else 0.0,
        },
        "assistant": {
            "home": str(paths.home()),
            "database": str(paths.db_path()),
            "logs": str(paths.logs_dir()),
        },
    }
    mac = _mac_product_version()
    if mac:
        info["macos"] = {
            "product_name": mac.get("ProductName"),
            "product_version": mac.get("ProductVersion"),
            "build": mac.get("BuildVersion"),
        }
    return info


register(ToolSpec(
    name="system_info", capability="system.info",
    description="Basic facts about this Mac: OS version, chip, free disk space.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_info,
    summarize=lambda a: "check basic system information",
))
