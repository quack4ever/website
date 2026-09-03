"""The security layer.

Nothing in this package may import from ``assistant.tools``,
``assistant.orchestrator`` or ``assistant.ai``.  The guard must not depend on
the things it guards - that one-way rule is what makes it impossible for a
tool (or a model) to route around the check.
"""
