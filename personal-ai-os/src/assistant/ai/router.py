"""The model router: choosing the right brain for each job.

ANALOGY
-------
A hospital does not send every patient to the neurosurgeon.  A nurse handles
a scraped knee; the specialist handles the hard case.  The router does the
same thing with models.

ROLES
-----
    reasoning - hard problems, the main conversation
    planning  - comparing strategies
    fast      - short, simple, high-volume work
    coding    - writing or reading code
    vision    - images and screenshots
    local     - anything that must not leave the Mac

Each role points at a *provider name* in your config, and each provider has
its own model.  Two roles can share one provider; that is the default.

FALLING BACK HONESTLY
---------------------
If the provider for a role is unavailable, the router does not silently
substitute a different model and pretend nothing happened.  It returns the
offline stub, which tells you exactly what is missing and how to fix it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import config as config_module
from ..errors import ConfigError
from ..logging_setup import get
from .anthropic_provider import AnthropicProvider
from .offline_provider import OfflineProvider
from .ollama_provider import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .provider import AIProvider

log = get(__name__)

PROVIDER_TYPES: Dict[str, Any] = {
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "openai_compat": OpenAICompatProvider,
    "offline": OfflineProvider,
}

ROLES = ("reasoning", "planning", "fast", "coding", "vision", "local")


class Router:
    def __init__(self, cfg: Optional[config_module.Config] = None) -> None:
        self.config = cfg or config_module.load()
        self._cache: Dict[str, AIProvider] = {}
        #: Set by tests (or by `--offline`) to force every role to one provider.
        self._override: Optional[AIProvider] = None

    def force(self, provider: AIProvider) -> None:
        """Point every role at one provider.  Used by tests and by --offline."""
        self._override = provider

    def build(self, provider_name: str) -> AIProvider:
        if provider_name in self._cache:
            return self._cache[provider_name]
        settings = (self.config.get("ai.providers." + provider_name) or {})
        if not settings:
            raise ConfigError(
                what="There is no provider called '%s' in your settings." % provider_name,
                why="ai.roles points at a provider that ai.providers does not define.",
                tried="Looking up ai.providers.%s" % provider_name,
                needs="A matching entry under ai.providers.",
                fix="Run 'assistant config show ai' to see what is defined.",
            )
        kind = str(settings.get("type") or provider_name)
        cls = PROVIDER_TYPES.get(kind)
        if cls is None:
            raise ConfigError(
                what="Unknown provider type '%s'." % kind,
                why="Supported types are: %s" % ", ".join(sorted(PROVIDER_TYPES)),
                tried="Building provider '%s'" % provider_name,
                needs="A supported type.",
                fix="Run:  assistant config set ai.providers.%s.type anthropic"
                    % provider_name,
            )
        instance = cls(settings)
        instance.name = provider_name
        self._cache[provider_name] = instance
        return instance

    def for_role(self, role: str = "reasoning") -> AIProvider:
        """Get the provider for a role, or the offline stub if it is unusable."""
        if self._override is not None:
            return self._override
        provider_name = self.config.get("ai.roles." + role) \
            or self.config.get("ai.roles.reasoning") or "offline"
        try:
            provider = self.build(str(provider_name))
        except ConfigError as exc:
            log.warning("role %s misconfigured: %s", role, exc.what)
            return self.build("offline")

        state = provider.availability()
        if not state.ok:
            log.info("role %s wanted '%s' but it is unavailable: %s",
                     role, provider_name, state.reason)
            stub = OfflineProvider({})
            stub.push_text(
                "I could not use the '%s' model for this (%s).\n\n%s"
                % (provider_name, state.reason, state.fix))
            return stub
        return provider

    # -- introspection -----------------------------------------------------
    def status(self) -> Dict[str, Any]:
        roles: Dict[str, Any] = {}
        for role in ROLES:
            name = self.config.get("ai.roles." + role)
            if not name:
                continue
            try:
                provider = self.build(str(name))
                roles[role] = dict(provider.describe(), provider=name)
            except ConfigError as exc:
                roles[role] = {"provider": name, "available": False,
                               "reason": exc.what, "fix": exc.fix}
        return {
            "roles": roles,
            "allow_cloud": self.config.allow_cloud,
            "any_available": any(r.get("available") for r in roles.values()),
        }

    def local_only(self) -> List[str]:
        """Which configured providers keep everything on this Mac."""
        out = []
        for name in (self.config.get("ai.providers") or {}):
            try:
                if self.build(name).is_local:
                    out.append(name)
            except ConfigError:
                continue
        return out
