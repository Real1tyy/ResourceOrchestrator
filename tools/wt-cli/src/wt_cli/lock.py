"""Stale `index.lock` recovery (spec-wt-unlock-stale-git-lock-recovery).

The safety property that lets `unlock` sit on the default-deny allowlist without
granting an arbitrary-delete capability: the lock path is **computed** via
``git rev-parse --git-path index.lock`` — which resolves the correct per-worktree
gitdir — and is **never** taken from input. A caller can only ever touch *that*
gitdir's lock. An age guard refuses a lock younger than
``STALE_LOCK_MIN_AGE_SECONDS``: a live op holds the lock for milliseconds, so a
fresh lock means a genuinely active op we must not race.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .const import STALE_LOCK_MIN_AGE_SECONDS, die
from .repo import git_out

# Markers git prints when an ``index.lock`` (NOT a merge conflict) stopped a rebase
# before it could begin — it can't detach HEAD without writing the index. Used to
# give an accurate diagnosis instead of conflict guidance.
_INDEX_LOCK_MARKERS = ("index.lock", "could not detach HEAD")


def is_index_lock_failure(output: str) -> bool:
    """True if rebase output shows a stale-lock failure rather than a merge conflict."""
    return any(marker in output for marker in _INDEX_LOCK_MARKERS)


def index_lock(cwd: Path | str | None = None) -> Path | None:
    """Absolute path to the ``index.lock`` for the gitdir at ``cwd`` if one exists
    on disk, else ``None``. The path is COMPUTED, never taken from input."""
    lock = git_out(["rev-parse", "--git-path", "index.lock"], cwd)
    lock_path = Path(lock)
    if not lock_path.is_absolute():
        lock_path = Path(cwd or os.getcwd()) / lock_path
    return lock_path if lock_path.is_file() else None


def clear_stale_index_lock(cwd: Path | str) -> bool:
    """Best-effort removal of a STALE ``index.lock`` blocking git at ``cwd``;
    returns whether one was cleared. A lock younger than
    ``STALE_LOCK_MIN_AGE_SECONDS`` may belong to a live op, so it is LEFT in place;
    the caller then surfaces it as a real failure rather than racing it."""
    lock = index_lock(cwd)
    if lock is None:
        return False
    age = time.time() - lock.stat().st_mtime
    if age < STALE_LOCK_MIN_AGE_SECONDS:
        return False
    lock.unlink()
    print(f"wt: cleared stale {lock} (was {age:.0f}s old) before rebasing")
    return True


def unlock(cwd: Path | str | None = None) -> None:
    """Remove a STALE git ``index.lock`` in the CURRENT worktree — recover from a
    git op interrupted mid-write that left its lock behind, blocking every
    subsequent add/commit/rebase. Refuses a fresh lock (a possibly-active op)."""
    lock = index_lock(cwd)
    if lock is None:
        print("wt: no index.lock present — nothing to unlock")
        return
    age = time.time() - lock.stat().st_mtime
    if age < STALE_LOCK_MIN_AGE_SECONDS:
        die(
            f"{lock} is {age:.1f}s old (< {STALE_LOCK_MIN_AGE_SECONDS:.0f}s) — a git "
            f"process may be active; not removing. If the op is truly dead, re-run "
            f"in a few seconds."
        )
    lock.unlink()
    print(f"wt: removed stale {lock} (was {age:.0f}s old)", file=sys.stderr)
