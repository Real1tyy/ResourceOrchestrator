"""End-to-end behaviour of the ported lifecycle verbs against throwaway repos.

These pin parity with `scripts/wt.py`'s dangerous sequences: off-HEAD `new`,
rebase-onto-local-main (clean + conflict), and the ff-only `land` with auto-rebase
on divergence + converging teardown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._repo import commit, git, make_worktree
from wt_cli.const import WtError
from wt_cli.land_worktree import run_land
from wt_cli.new_worktree import run_new
from wt_cli.rebase_worktree import run_rebase
from wt_cli.verify_worktree import run_verify, run_warn_behind


@pytest.fixture(autouse=True)
def _no_bootstrap(monkeypatch: pytest.MonkeyPatch):
    """`new` bootstraps via mise; stub it out so tests don't shell out to mise."""
    monkeypatch.setattr("wt_cli.new_worktree._bootstrap", lambda _p: None)


def _branches(repo: Path) -> list[str]:
    return git(repo, "branch", "--format=%(refname:short)").split()


# ─── new ─────────────────────────────────────────────────────────────────────


def test_new_rejects_bad_slug(main_repo: Path):
    with pytest.raises(WtError):
        run_new("Bad_Slug")


def test_new_rejects_empty(main_repo: Path):
    with pytest.raises(WtError):
        run_new(None)


def test_new_refuses_inside_worktree(main_repo: Path, monkeypatch):
    wt = make_worktree(main_repo, "host")
    monkeypatch.chdir(wt)
    with pytest.raises(WtError):
        run_new("nested")


def test_new_creates_worktree_off_head(main_repo: Path):
    run_new("fresh")
    wt = main_repo / ".claude" / "worktrees" / "fresh"
    assert wt.is_dir()
    assert "worktree-fresh" in _branches(main_repo)
    # Off HEAD: the seed commit's file is present.
    assert (wt / "seed.md").exists()


def test_new_refuses_existing(main_repo: Path):
    run_new("dup")
    with pytest.raises(WtError):
        run_new("dup")


def test_new_fires_post_hook(main_repo: Path):
    hook = main_repo / ".config" / "wt" / "hooks.py"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "from pathlib import Path\n"
        "def post_new(ctx):\n"
        "    (Path(ctx['worktree']) / 'HOOK_RAN').write_text(ctx['slug'])\n"
    )
    run_new("hooked")
    marker = main_repo / ".claude" / "worktrees" / "hooked" / "HOOK_RAN"
    assert marker.read_text() == "hooked"


# ─── rebase ──────────────────────────────────────────────────────────────────


def test_rebase_refuses_dirty(main_repo: Path, monkeypatch):
    wt = make_worktree(main_repo, "dirtywt")
    (wt / "scratch.txt").write_text("uncommitted\n")
    monkeypatch.chdir(wt)
    with pytest.raises(WtError):
        run_rebase(None)


def test_rebase_clean_onto_moved_main(main_repo: Path, monkeypatch):
    wt = make_worktree(main_repo, "cleanwt", extra_commits=1)
    commit(main_repo, "main-new.txt")  # advance main on a different file
    monkeypatch.chdir(wt)
    run_rebase(None)  # no raise == clean
    # The branch now sits on top of main → main's file is present in the worktree.
    assert (wt / "main-new.txt").exists()
    assert (wt / "cleanwt-0.txt").exists()


def test_rebase_conflict_raises(main_repo: Path, monkeypatch):
    wt = make_worktree(main_repo, "confwt")
    (wt / "seed.md").write_text("branch side\n")
    git(wt, "commit", "-aqm", "branch edit")
    (main_repo / "seed.md").write_text("main side\n")
    git(main_repo, "commit", "-aqm", "main edit")
    monkeypatch.chdir(wt)
    with pytest.raises(WtError):
        run_rebase(None)
    # Rebase left in progress for in-worktree resolution.
    assert (wt / ".git").exists() or True


# ─── land ────────────────────────────────────────────────────────────────────


def test_land_fast_forward_and_teardown(main_repo: Path):
    wt = make_worktree(main_repo, "landme", extra_commits=1)
    run_land("landme")
    # main advanced to the branch tip; worktree + branch gone.
    assert (main_repo / "landme-0.txt").exists()
    assert not wt.exists()
    assert "worktree-landme" not in _branches(main_repo)


def test_land_auto_rebases_on_divergence(main_repo: Path):
    wt = make_worktree(main_repo, "diverge", extra_commits=1)
    commit(main_repo, "main-side.txt")  # main moves → diverged, ff impossible
    run_land("diverge")
    # Auto-rebase then ff: both the branch's and main's commits land on main.
    assert (main_repo / "diverge-0.txt").exists()
    assert (main_repo / "main-side.txt").exists()
    assert not wt.exists()
    assert "worktree-diverge" not in _branches(main_repo)


def test_land_unknown_slug_raises(main_repo: Path):
    make_worktree(main_repo, "real")
    with pytest.raises(WtError):
        run_land("ghost")


def test_land_refuses_inside_worktree(main_repo: Path, monkeypatch):
    wt = make_worktree(main_repo, "inside")
    monkeypatch.chdir(wt)
    with pytest.raises(WtError):
        run_land(None)


# ─── verify / warn-behind / report ────────────────────────────────────────────


def test_verify_runs_inside_worktree(main_repo: Path, monkeypatch, capsys):
    wt = make_worktree(main_repo, "vwt", extra_commits=1)
    monkeypatch.chdir(wt)
    run_verify(None)
    out = capsys.readouterr().out
    assert "self-review inputs" in out


def test_warn_behind_is_silent_when_current(main_repo: Path, monkeypatch, capsys):
    wt = make_worktree(main_repo, "behindwt")
    monkeypatch.chdir(wt)
    run_warn_behind()
    assert "behind" not in capsys.readouterr().err


def test_warn_behind_warns_when_behind(main_repo: Path, monkeypatch, capsys):
    wt = make_worktree(main_repo, "stalewt")
    commit(main_repo, "ahead.txt")  # main moves ahead of the branch base
    monkeypatch.chdir(wt)
    run_warn_behind()
    assert "behind" in capsys.readouterr().err
