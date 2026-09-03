"""The tool layer - the assistant's hands.

Every physical effect the system can have on your Mac lives in this package,
and every one of them is routed through ``registry.execute()``, which calls the
policy engine first.  If you want to audit what this software can possibly do,
read this directory: there is nothing else.
"""
from .registry import (ExecContext, ToolResult, ToolSpec, execute, get,  # noqa: F401
                       list_specs, provider_schemas, register)

__all__ = ["ExecContext", "ToolResult", "ToolSpec", "execute", "get",
           "list_specs", "provider_schemas", "register", "load_all"]


def load_all() -> None:
    """Import every tool module so they register themselves.

    Called once at startup.  Kept as a function (not import-time side effects)
    so tests can control exactly when registration happens.
    """
    from . import files, shell, system  # noqa: F401
    from . import index_tools, memory_tools, macos_tools  # noqa: F401
