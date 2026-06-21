#!/usr/bin/env python3
"""guard_commit_location.py — block commits made from the main checkout (spec 0031, R3).

Commits happen ONLY inside a worktree under .claude/worktrees/. The only
sanctioned mutation of ``main`` is the human's Stage-8 fast-forward + teardown
(``mise run wt:land``), which is a ``merge --ff-only``, not a commit — so this
guard never fires on it.

This is the SECOND of two enforcement points (defense in depth). The first is the
default-deny PreToolUse hook, which blocks an agent's ``git commit`` Bash call.
This guard runs from lefthook's pre-commit stage and catches commits made from
any shell (agent or human) regardless of the hook.

Human escape hatch (documented in docs/TOOLS.md §12): set
``AGENTIC_ALLOW_MAIN_COMMIT=1`` to permit a one-off commit on the main checkout.

Stdlib only — no dependencies.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Depth-independent shared-lib import (spec-scripts-shared-lib): walk up to the
# scripts root (the dir holding lib/) so this resolves whether the script sits at
# scripts/foo.py or scripts/<sub>/foo.py.
for _ancestor in Path(__file__).resolve().parents:
    if (_ancestor / "lib" / "__init__.py").is_file():
        sys.path.insert(0, str(_ancestor))
        break
import lib  # noqa: E402

WORKTREE_DIR = ".claude/worktrees"


def main() -> int:
    if os.environ.get("AGENTIC_ALLOW_MAIN_COMMIT") == "1":
        print(
            "guard-commit: AGENTIC_ALLOW_MAIN_COMMIT=1 — allowing commit on main checkout",
            file=sys.stderr,
        )
        return 0

    result = lib.git(["rev-parse", "--show-toplevel"], check=False)
    toplevel = result.stdout.strip()

    if f"/{WORKTREE_DIR}/" in toplevel.replace("\\", "/") + "/":
        # Inside a worktree under .claude/worktrees/ — commits are expected here.
        return 0

    print(
        f"""guard-commit: BLOCKED — no commits on the main checkout (spec 0031).

  Toplevel: {toplevel or "<unknown>"}

  Commits happen inside a worktree under {WORKTREE_DIR}/. Create or enter one:
      mise run wt:new <slug>
      cd {WORKTREE_DIR}/<slug>

  The only sanctioned change to main is the Stage-8 fast-forward:
      mise run wt:land <slug>

  Human escape hatch (one-off): AGENTIC_ALLOW_MAIN_COMMIT=1 git commit ...""",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
