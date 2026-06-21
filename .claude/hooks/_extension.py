"""Repo-local extension dispatch for the harness hooks (template-method pattern).

Every harness hook calls ``invoke_extension(<stem>, "pre"|"post", data)`` at the head
and tail of its work. The call loads the repo-local module
``.claude/hooks/extensions/<stem>.py`` and runs its ``pre(data)`` / ``post(data)``.

Those extension modules are **seeded as no-op stubs by ``agenticd init`` and then
OWNED by the repo** — init never overwrites them. That is the whole point: a repo
bolts on machine-specific gates (e.g. "don't edit a shipped DB migration") *here*,
without forking the ported harness hook, so repo-specific policy never leaks into the
bundle that `agenticd init` pushes to every other repo ([[decision-hook-extension-points]]).

Contract:
- ``pre`` runs **before** the harness hook's built-in logic; ``post`` **after** it
  has passed. A hook with no meaningful post phase may call only ``pre``.
- To **block**, an extension prints a reason to stderr and ``sys.exit(2)`` (raising
  ``SystemExit``); that propagates unchanged through the harness hook, so the repo
  rule reads exactly like a built-in guard.
- Everything else is a **silent no-op**: no module, no such function, or any
  import/exec/logic error inside the extension. Extensions only ever ADD behavior — a
  broken or absent one must never brick the harness hook.

``data`` is the parsed hook JSON payload (for PreToolUse: ``tool_name``,
``tool_input``, ``cwd``, …); extensions read it but must not mutate harness control
flow except by blocking.
"""

import importlib.util
from pathlib import Path

_EXTENSIONS_DIR = Path(__file__).resolve().parent / "extensions"


def invoke_extension(stem, phase, data):
    """Run the repo-local ``extensions/<stem>.py`` ``<phase>(data)`` hook, if present.

    Re-raises ``SystemExit`` (an extension's deliberate block); swallows every other
    failure so a missing/broken extension is a no-op."""
    ext_path = _EXTENSIONS_DIR / f"{stem}.py"
    if not ext_path.is_file():
        return
    try:
        spec = importlib.util.spec_from_file_location(f"_agentic_ext_{stem}_{phase}", ext_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, phase, None)
        if fn is not None:
            fn(data)
    except SystemExit:
        raise
    except Exception:
        return
