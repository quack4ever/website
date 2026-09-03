# DEVELOPMENT

## Running from a checkout, without installing

```bash
cd personal-ai-os
export PAIOS_HOME=/tmp/paios-dev      # keep your real data safe
./bin/assistant status
```

`PAIOS_HOME` redirects everything — database, config, logs, socket. The test
suite uses it so tests can never touch a real installation.

## Tests

```bash
PYTHONPATH=src pytest tests/ -q               # all 273
PYTHONPATH=src pytest tests/ -q -m "not slow" # skip the real-installer tests
PYTHONPATH=src pytest tests/test_security.py -v
```

They run on macOS and Linux. The macOS-only bridges are tested for their
*properties* (argv safety, plist shape, error translation) rather than by
executing AppleScript, so the security-critical parts are covered everywhere.

## Layout, and the one rule that matters

```
src/assistant/
  paths.py config.py db.py audit.py errors.py redact.py logging_setup.py
  security/    capabilities policy pathguard shellguard injection consent killswitch
  tools/       registry schema files shell system macos_tools memory_tools index_tools
  ai/          provider anthropic_provider ollama_provider openai_compat offline router
  index/       extract indexer search dedupe
  memory/      store
  planner/     superposition beam plan
  orchestrator/ agent roles reflect
  macos/       osa spotlight tcc launchd
  daemon/      server handlers protocol scheduler
  cli/         main render
  diagnostics/ doctor
```

**Dependencies point one way only.** `tools/` may import `security/`;
`security/` may never import `tools/`, `ai/`, `orchestrator/`, `daemon/` or
`cli/`. That is what makes the permission check impossible to route around, and
`tests/test_packaging.py` fails the build if it is ever violated.

## Adding a tool

1. Add the capability to `security/capabilities.py` with an honest risk tier.
   Unknown capabilities are treated as CRITICAL, so this step is not optional.
2. Write the handler. Assume you are already authorised — `registry.execute()`
   did that. Raise `AssistantError` subclasses with all five fields filled in.
3. Register it with a JSON schema, `path_args` naming any path arguments, and a
   `summarize` function that writes the sentence a person will read on the
   approval slip.
4. Add it to `tools/__init__.py:load_all()` if it is a new module.
5. Test it: valid arguments, invalid arguments, denied by policy, and the
   approval path.

```python
register(ToolSpec(
    name="my_tool",
    capability="files.read",
    description="What it does. Written for a model to read.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"], "additionalProperties": False},
    handler=_my_handler,
    path_args=("path",),
    summarize=lambda a: "read %s" % a.get("path"),
))
```

If a tool returns content that came off the disk, wrap it with
`injection.wrap()` and put the wrapped version in an `untrusted` key. The
orchestrator only ever shows the model that key.

## Adding an AI provider

Subclass `ai.provider.AIProvider`, implement `availability()` and `complete()`,
register it in `ai/router.py:PROVIDER_TYPES`. `availability()` must never raise,
and when it returns False the `reason` and `fix` are shown to the user verbatim
— write them as instructions, not error codes.

## House style

- **Python 3.9.** macOS ships 3.9.6. No `match`, no `X | Y` runtime annotations,
  no `tomllib`. A test enforces this across the whole codebase.
- **Standard library only** in the core. `anthropic` is optional and lazily
  imported.
- **Never fail silently.** Every `AssistantError` fills in what/why/tried/needs/fix.
- **Comments explain *why*.** The what is in the code.
- **No fake functionality.** If something is not implemented, say so in the
  error message and in `docs/STATUS.md`. Do not stub it and move on.

## Debugging

```bash
PAIOS_HOME=/tmp/paios-dev ./bin/assistant --local status   # bypass the daemon
PAIOS_HOME=/tmp/paios-dev python3 -m assistant.daemon --serve   # daemon in the foreground
assistant logs --event 'policy.*'                          # just permission decisions
tail -f ~/Library/Logs/PersonalAIOS/assistant.log
```

`--local` is the useful one: it runs everything in your process so you get a
real traceback instead of a serialised error.

## Adding a database table

Append a new string to `MIGRATIONS` in `db.py`. Never edit an existing one — the
`meta.schema_version` row records what has run, and each migration is applied
inside its own transaction.
