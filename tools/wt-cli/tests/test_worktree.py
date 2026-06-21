"""Landable-worktree discovery + selection + the Worktree value type."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._repo import commit, git, make_worktree
from wt_cli.const import WtError
from wt_cli.worktree import Worktree, list_worktrees, resolve_worktree


def _wt(ahead: int, behind: int) -> Worktree:
    return Worktree("s", "worktree-s", Path("/x"), ahead, behind)


def test_fast_forwardable_only_when_behind_zero_and_ahead():
    assert _wt(2, 0).fast_forwardable
    assert not _wt(0, 0).fast_forwardable
    assert not _wt(2, 1).fast_forwardable


def test_status_note_wording():
    assert "nothing to land" in _wt(0, 0).status_note()
    assert "needs rebase" in _wt(1, 3).status_note()
    assert _wt(2, 0).status_note() == "ready to land"


def test_list_worktrees_filters_to_convention(main_repo: Path):
    make_worktree(main_repo, "alpha", extra_commits=2)
    # A foreign worktree (not the worktree-<slug> convention, not under .claude/) is ignored.
    git(main_repo, "worktree", "add", "-q", "-b", "feature-x", "../foreign", "HEAD")

    wts = list_worktrees(main_repo)
    assert [w.slug for w in wts] == ["alpha"]
    assert wts[0].branch == "worktree-alpha"
    assert wts[0].ahead == 2
    assert wts[0].behind == 0


def test_list_worktrees_reports_behind(main_repo: Path):
    wt = make_worktree(main_repo, "beta", extra_commits=0)
    # Advance main beyond the branch's base → the worktree is now behind.
    commit(main_repo, "main-moved.txt")
    (only,) = list_worktrees(main_repo)
    assert only.slug == "beta"
    assert only.behind == 1
    assert only.ahead == 0
    assert not only.fast_forwardable
    assert wt.exists()


def test_resolve_worktree_inside_returns_self(main_repo: Path, monkeypatch):
    wt = make_worktree(main_repo, "gamma")
    monkeypatch.chdir(wt)
    resolved = resolve_worktree(None, "rebase")
    assert resolved.resolve() == wt.resolve()


def test_resolve_worktree_on_main_by_slug(main_repo: Path):
    wt = make_worktree(main_repo, "delta")
    resolved = resolve_worktree("delta", "rebase")
    assert resolved.resolve() == wt.resolve()


def test_resolve_worktree_unknown_slug_raises(main_repo: Path):
    make_worktree(main_repo, "delta")
    with pytest.raises(WtError):
        resolve_worktree("nope", "rebase")


def test_resolve_worktree_none_available_raises(main_repo: Path):
    with pytest.raises(WtError):
        resolve_worktree("anything", "rebase")
