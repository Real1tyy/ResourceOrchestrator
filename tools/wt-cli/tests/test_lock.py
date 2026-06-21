"""Stale index.lock recovery — the safety-critical bits ported from scripts/wt.py.

The properties pinned here are why `unlock` can sit on the default-deny allowlist:
the lock path is COMPUTED (never from input) and an age guard refuses a fresh lock
(a possibly-live op).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from wt_cli.const import STALE_LOCK_MIN_AGE_SECONDS, WtError
from wt_cli.lock import (
    clear_stale_index_lock,
    index_lock,
    is_index_lock_failure,
    unlock,
)


def _lock_path(repo: Path) -> Path:
    return repo / ".git" / "index.lock"


def _make_lock(repo: Path, *, age_seconds: float) -> Path:
    lock = _lock_path(repo)
    lock.write_text("")
    old = time.time() - age_seconds
    os.utime(lock, (old, old))
    return lock


def test_index_lock_none_when_absent(main_repo: Path):
    assert index_lock(main_repo) is None


def test_index_lock_found_when_present(main_repo: Path):
    lock = _make_lock(main_repo, age_seconds=0)
    found = index_lock(main_repo)
    assert found is not None
    assert found.resolve() == lock.resolve()


def test_clear_stale_removes_old_lock(main_repo: Path):
    lock = _make_lock(main_repo, age_seconds=STALE_LOCK_MIN_AGE_SECONDS + 10)
    assert clear_stale_index_lock(main_repo) is True
    assert not lock.exists()


def test_clear_stale_leaves_fresh_lock(main_repo: Path):
    lock = _make_lock(main_repo, age_seconds=0)
    assert clear_stale_index_lock(main_repo) is False
    assert lock.exists()


def test_unlock_removes_stale(main_repo: Path):
    lock = _make_lock(main_repo, age_seconds=STALE_LOCK_MIN_AGE_SECONDS + 10)
    unlock(main_repo)
    assert not lock.exists()


def test_unlock_refuses_fresh(main_repo: Path):
    lock = _make_lock(main_repo, age_seconds=0)
    with pytest.raises(WtError):
        unlock(main_repo)
    assert lock.exists()


def test_unlock_noop_when_absent(main_repo: Path):
    # No lock present → no exception, nothing removed.
    unlock(main_repo)


def test_is_index_lock_failure_markers():
    assert is_index_lock_failure("fatal: Unable to create '.../index.lock': File exists")
    assert is_index_lock_failure("error: could not detach HEAD")
    assert not is_index_lock_failure("CONFLICT (content): Merge conflict in foo.txt")
