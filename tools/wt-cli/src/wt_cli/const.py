"""Shared constants + the lifecycle error type.

The worktree-lifecycle behaviour is a clean port of `scripts/wt.py` (the
behavioural source of truth). These constants carry the same values; changing
one here is a behavioural change and must stay in step with that source until
the harness wiring is flipped (see spec-wt-cli-lifecycle-port).
"""

from __future__ import annotations

import re
from typing import NoReturn

WORKTREE_DIR = ".claude/worktrees"
BRANCH_PREFIX = "worktree-"

# A slug is lowercase alphanumerics joined by single hyphens.
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Age floor below which an index.lock is treated as a possibly-live git op and
# NOT removed. A real op holds the lock for milliseconds, so anything older than
# a few seconds is stale; the floor stops `unlock`/`rebase` from yanking the lock
# out from under an active rebase/commit (spec-wt-unlock-stale-git-lock-recovery).
STALE_LOCK_MIN_AGE_SECONDS = 6.0


class WtError(Exception):
    """A lifecycle command failed with an operator-facing message.

    Carries the guidance text (often multi-line). `cli.py` catches it, prints
    ``wt: <message>``, and exits non-zero — the structured analogue of
    `scripts/wt.py`'s ``die()`` that keeps the lifecycle functions testable
    (a test asserts ``WtError`` is raised, not that the process exited)."""


def die(message: str) -> NoReturn:
    raise WtError(message)
