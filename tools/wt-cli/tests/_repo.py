"""Throwaway-git-repo helpers for the lifecycle test suite.

Stand up repos that mirror the layout the lifecycle code expects: a `main` branch
and linked worktrees under `.claude/worktrees/<slug>` on `worktree-<slug>` branches.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "config", "user.email", "t@e")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "seed.md").write_text("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")


def commit(repo: Path, name: str, content: str = "x\n") -> None:
    (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"add {name}")


def make_worktree(repo: Path, slug: str, *, extra_commits: int = 0) -> Path:
    """Add a linked worktree under `<repo>/.claude/worktrees/<slug>` on branch
    `worktree-<slug>` off HEAD, optionally with N commits beyond main."""
    rel = f".claude/worktrees/{slug}"
    branch = f"worktree-{slug}"
    git(repo, "worktree", "add", "-q", "-b", branch, rel, "HEAD")
    wt = repo / rel
    for i in range(extra_commits):
        commit(wt, f"{slug}-{i}.txt")
    return wt
