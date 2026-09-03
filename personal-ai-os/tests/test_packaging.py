"""Structural tests for the package itself.

These catch whole classes of mistake that are easy to make and horrible to
debug, so they are worth enforcing automatically rather than by memory.
"""
from __future__ import annotations

import importlib
import pkgutil
import types

import pytest

import assistant


def _packages():
    for info in pkgutil.walk_packages(assistant.__path__, "assistant."):
        if info.ispkg:
            yield importlib.import_module(info.name)


def test_no_exported_name_shadows_a_submodule():
    """`from .thing import thing` in an __init__ hides the `thing` MODULE.

    This bit us twice during development: `assistant.orchestrator.reflect` and
    `assistant.cli.main` both silently became functions, so importing the
    module gave you something that looked right and failed later.
    """
    problems = []
    for package in _packages():
        submodules = {m.name.split(".")[-1] for m in pkgutil.iter_modules(package.__path__)}
        for name in submodules:
            attribute = getattr(package, name, None)
            if attribute is not None and not isinstance(attribute, types.ModuleType):
                problems.append("%s.%s is a %s, hiding the submodule of that name"
                                % (package.__name__, name, type(attribute).__name__))
    assert not problems, "\n".join(problems)


def test_every_name_in_dunder_all_actually_exists():
    """An __all__ entry that is not imported breaks `from x import *`."""
    problems = []
    for package in _packages():
        for name in getattr(package, "__all__", []):
            if not hasattr(package, name):
                problems.append("%s.__all__ lists %r but it is not defined"
                                % (package.__name__, name))
    assert not problems, "\n".join(problems)


def test_every_module_imports_cleanly():
    """A module that only fails at import time in production is a bad surprise."""
    failures = []
    for info in pkgutil.walk_packages(assistant.__path__, "assistant."):
        if info.name.endswith("__main__"):
            continue      # those parse args and exit
        try:
            importlib.import_module(info.name)
        except Exception as exc:
            failures.append("%s: %s: %s" % (info.name, type(exc).__name__, exc))
    assert not failures, "\n".join(failures)


def test_security_layer_does_not_import_the_layers_it_guards():
    """The guard must not depend on the things it guards.

    If security/ could import tools/ or orchestrator/, a circular dependency
    would eventually tempt someone into an import that lets a tool reach in and
    change a policy decision. Keeping the arrow one-way is what makes the
    check un-routable-around.
    """
    import ast
    import pathlib
    root = pathlib.Path(assistant.__file__).parent / "security"
    forbidden = ("assistant.tools", "assistant.orchestrator", "assistant.ai",
                 "assistant.daemon", "assistant.cli")
    problems = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # Resolve a relative import to its absolute name.
                if node.level:
                    prefix = "assistant." if node.level == 2 else "assistant.security."
                    full = prefix + node.module
                else:
                    full = node.module
                if any(full.startswith(f) for f in forbidden):
                    problems.append("%s imports %s" % (path.name, full))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(f) for f in forbidden):
                        problems.append("%s imports %s" % (path.name, alias.name))
    assert not problems, "security layer must not import what it guards:\n" + \
        "\n".join(problems)


def test_python_39_compatible_syntax():
    """macOS ships Python 3.9, so nothing may use 3.10+ only syntax.

    We compile every file with the oldest supported feature version; anything
    newer (match statements, `int | None` in a runtime annotation) fails here
    instead of on a user's Mac.
    """
    import ast
    import pathlib
    root = pathlib.Path(assistant.__file__).parent
    failures = []
    for path in sorted(root.rglob("*.py")):
        try:
            # feature_version is an ast.parse argument, not a compile() one.
            ast.parse(path.read_text(), str(path), feature_version=(3, 9))
        except SyntaxError as exc:
            failures.append("%s:%s %s" % (path.name, exc.lineno, exc.msg))
    assert not failures, "not valid on Python 3.9:\n" + "\n".join(failures)
