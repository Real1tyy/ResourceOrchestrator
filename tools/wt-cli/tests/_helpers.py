"""Shared test helpers for wt-cli tests.

Provides the argv-builder and env-builder needed to invoke the `wt` CLI
in a subprocess without requiring a global `uv tool install`. Mirrors how
production `wt` is invoked, but via `python -m wt_cli.cli` with
`PYTHONPATH=src/` injected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PY = sys.executable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"


def cli_argv(subcommand: str, *args: str) -> list[str]:
    """Build the argv for invoking `wt <subcommand>` via the package entry."""
    return [PY, "-m", "wt_cli.cli", subcommand, *args]


def cli_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess env that can resolve `import wt_cli`."""
    env = dict(extra) if extra else dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC}:{existing}" if existing else str(SRC)
    return env


def run(
    cmd: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Shared subprocess runner used across integration tests.

    Always captures stdout/stderr, runs text-mode, and stringifies `cwd`
    so callers can pass a `Path`. `input_text` is for the few tests that
    pipe stdin (notably the merge confirmation prompt).
    """
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=check,
        capture_output=True,
        text=True,
        env=env,
        input=input_text,
    )
