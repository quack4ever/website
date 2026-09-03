"""The command-line interface.

Submodules are imported FIRST. Doing `from .main import main` before this
would bind the name `main` to the function and hide the `main` module, so
`from assistant.cli import main` would hand you a function where a module was
expected - which is a confusing failure to debug. The function is re-exported
as `run` instead. (`tests/test_packaging.py` enforces this project-wide.)
"""
from . import main, render  # noqa: F401
from .main import build_parser  # noqa: F401
from .main import main as run  # noqa: F401

__all__ = ["main", "render", "build_parser", "run"]
