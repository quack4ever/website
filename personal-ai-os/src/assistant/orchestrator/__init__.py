"""The orchestrator: perceive, think, act through the guard, reflect."""
# Import the submodules FIRST. Doing `from .reflect import reflect` before this
# would bind the name `reflect` to the function and hide the module, so
# `from assistant.orchestrator import reflect` would hand you a function where
# a module was expected. Submodule first, function under a distinct alias.
from . import agent, reflect  # noqa: F401
from .agent import Agent, AgentResult, extract_json  # noqa: F401
from .reflect import reflect as reflect_on  # noqa: F401
from .reflect import success_rate  # noqa: F401

__all__ = ["agent", "reflect", "Agent", "AgentResult", "extract_json",
           "reflect_on", "success_rate"]
