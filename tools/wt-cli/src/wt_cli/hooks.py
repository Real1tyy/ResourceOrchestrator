"""Generalized pre/post lifecycle hook seam.

This replaces the tool's old, opinionated `link-deps` (node_modules symlinking)
with a generic extension point — the same template/extension pattern the harness
pre-commit hooks use ([[decision-hook-extension-points]]) and that
`scripts/wt.py` already models with `_run_local_hook` → `scripts/wt/wt_local.py`.

The tool ships a **no-op default**: with no override file present, every hook is a
silent no-op. A **repo-owned** override file (default ``<repo>/.config/wt/hooks.py``,
overridable via ``$WT_HOOKS_PATH``) can ADD behaviour per lifecycle stage. Because
that file is never part of the `wt-cli` package payload, no install or sync ever
overwrites a downstream's edits — the seed-once property holds by construction.

Contract (matching the harness extension seam):
  * A missing file, a missing stage function, or **any exception** inside a hook is
    a silent no-op — an extension only ever *adds* behaviour, never breaks the
    lifecycle op (the committed work is already safe).
  * A hook **blocks** its stage by exiting non-zero (``sys.exit(2)`` /
    ``raise SystemExit(2)``); the lifecycle command then aborts with that as the
    cause.

A downstream re-adds the old node_modules symlinking by implementing, e.g.::

    # <repo>/.config/wt/hooks.py
    def post_new(ctx):
        link_node_modules(ctx["main_root"], ctx["worktree"])

Stages fire ``pre_<stage>`` before and ``post_<stage>`` after the git work, or a
generic ``pre(stage, ctx)`` / ``post(stage, ctx)`` if the specific name is absent.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from .const import die

DEFAULT_HOOKS_REL = ".config/wt/hooks.py"


def hook_path(repo: Path) -> Path:
    """Resolve the repo-local override file. ``$WT_HOOKS_PATH`` wins (absolute, or
    relative to ``repo``); otherwise the default ``<repo>/.config/wt/hooks.py``."""
    override = os.environ.get("WT_HOOKS_PATH")
    if override:
        p = Path(override)
        return p if p.is_absolute() else repo / p
    return repo / DEFAULT_HOOKS_REL


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("wt_hooks_ext", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(stage: str, phase: str, *, repo: Path, ctx: dict | None = None) -> None:
    """Fire the repo-local ``{phase}_{stage}`` hook, best-effort.

    ``phase`` is ``"pre"`` or ``"post"``. ``ctx`` is passed to the hook (keys
    depend on the stage, e.g. ``slug``/``branch``/``worktree``/``main_root``).
    """
    ctx = ctx or {}
    path = hook_path(repo)
    if not path.is_file():
        return
    try:
        module = _load(path)
    except Exception as exc:  # a broken extension must not break the lifecycle op
        print(f"wt: hook file load skipped ({exc})", file=sys.stderr)
        return
    if module is None:
        return

    specific = getattr(module, f"{phase}_{stage}", None)
    generic = getattr(module, phase, None)
    try:
        if callable(specific):
            specific(ctx)
        elif callable(generic):
            generic(stage, ctx)
        else:
            return
    except SystemExit as exc:  # a hook may BLOCK its stage with a non-zero exit
        code = exc.code
        rc = code if isinstance(code, int) else (0 if code is None else 1)
        if rc != 0:
            die(f"{phase}_{stage} hook blocked the '{stage}' stage (exit {rc})")
    except Exception as exc:  # any other failure is advisory-only — never fatal
        print(f"wt: {phase}_{stage} hook skipped ({exc})", file=sys.stderr)
