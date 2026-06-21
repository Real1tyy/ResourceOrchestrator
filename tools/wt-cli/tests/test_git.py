"""`wt_cli.git` — subprocess wrappers + repo discovery primitives.

The `git()` wrapper forces `core.quotePath=false`; `main_repo_of()`
resolves the main repo for any worktree (or repo itself). Both are
load-bearing for every other module — keep them tested.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wt_cli.git import git, git_text, main_repo_of, repo_root


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "seed.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


# ─── git / git_text ────────────────────────────────────────────────────────


def test_git_text_returns_decoded_stdout(tmp_git_repo: Path):
    out = git_text(["rev-parse", "HEAD"], tmp_git_repo)
    assert len(out.strip()) >= 40


def test_git_forces_core_quotepath_false(tmp_git_repo: Path):
    """Non-ASCII paths must round-trip as raw UTF-8 in name-status, not
    as C-escaped `\\303\\275`. This is what the explicit `-c core.quotePath=false`
    in `git()` is there for."""
    unicode_name = "notes/starý.md"
    (tmp_git_repo / "notes").mkdir()
    (tmp_git_repo / unicode_name).write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)

    out = git_text(["diff", "--cached", "--name-only"], tmp_git_repo)
    assert unicode_name in out, "core.quotePath=false should keep raw UTF-8"


def test_git_check_true_raises_on_failure(tmp_git_repo: Path):
    with pytest.raises(subprocess.CalledProcessError):
        git(["bogus-subcommand"], tmp_git_repo)


def test_git_check_false_returns_proc(tmp_git_repo: Path):
    proc = git(["bogus-subcommand"], tmp_git_repo, check=False)
    assert proc.returncode != 0


# ─── repo_root ─────────────────────────────────────────────────────────────


def test_repo_root_returns_absolute_path(tmp_git_repo: Path, monkeypatch):
    monkeypatch.chdir(tmp_git_repo)
    root = repo_root()
    assert root.is_absolute()
    assert root.resolve() == tmp_git_repo.resolve()


# ─── main_repo_of ──────────────────────────────────────────────────────────


def test_main_repo_of_in_main_repo_returns_self(tmp_git_repo: Path):
    """`git rev-parse --git-common-dir` resolves to `.git` (relative) inside the
    main repo. `main_repo_of` joins with cwd and returns its parent."""
    found = main_repo_of(tmp_git_repo)
    assert found is not None
    assert found.resolve() == tmp_git_repo.resolve()


def test_main_repo_of_in_linked_worktree_returns_main(
    tmp_git_repo: Path, tmp_path: Path
):
    """From inside a linked worktree, common-dir is an absolute path back to
    `<main>/.git`; the parent is the main repo."""
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(wt), "main"],
        cwd=tmp_git_repo,
        check=True,
    )

    found = main_repo_of(wt)
    assert found is not None
    assert found.resolve() == tmp_git_repo.resolve()


def test_main_repo_of_returns_none_outside_git(tmp_path: Path):
    """Run from a non-git directory — `git rev-parse` exits non-zero. Helper
    must catch the CalledProcessError and return None."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main_repo_of(plain) is None
