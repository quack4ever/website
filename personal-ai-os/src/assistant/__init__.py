"""Personal AI OS - a permissioned AI operating layer for macOS.

Import order matters here: this package is layered, and lower layers must never
import higher ones.  ``security`` never imports ``tools``; ``tools`` never
imports ``orchestrator``.  That one-way dependency rule is what makes the
permission guard impossible to route around.
"""
from .version import __version__

__all__ = ["__version__"]
