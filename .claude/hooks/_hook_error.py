#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Shared error-logging tail for every Claude Code hook in this directory.

A Claude Code hook that crashes loudly breaks the user's session, so every
hook in this payload swallows its exceptions and exits 0. The price of that
safety is invisibility -- a disk-full, a permission error on ``logs/``, a
malformed transcript, or a refactor that lands a ``TypeError`` all disappear
with no trail.

``fail_safe(exc, hook_name)`` is the small wrapper that keeps the session
alive *and* leaves breadcrumbs. Each hook's terminal ``except`` clause becomes
``except Exception as exc: fail_safe(exc, "<name>")``. Errors land in
``logs/.hook-errors.log`` as one timestamped line each, tail-able and
grep-able.

Robustness: this module's own logging is itself wrapped in ``try/except`` so a
read-only filesystem (or any other failure path) still exits 0.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

ERROR_LOG_NAME = ".hook-errors.log"


def fail_safe(exc: BaseException, hook_name: str) -> None:
    """Record ``exc`` against ``hook_name`` in ``logs/.hook-errors.log``, then exit 0.

    Never raises. Suitable as the final clause of a hook's outermost
    ``try/except``: ``except Exception as exc: fail_safe(exc, "stop")``.
    """
    try:
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        with (log_dir / ERROR_LOG_NAME).open("a", encoding="utf-8") as f:
            f.write(f"{ts} | {hook_name} | {type(exc).__name__}: {exc}\n")
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            for line in tb.rstrip().splitlines():
                f.write(f"    {line}\n")
    except Exception:
        # Logging must never break the session. Silence is correct here -- the
        # outer ``sys.exit(0)`` is the contract this helper exists to uphold.
        pass
    sys.exit(0)
