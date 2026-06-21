"""Landable-worktree discovery + interactive selection.

A *landable* worktree is a linked worktree under ``.claude/worktrees/`` whose
branch follows the ``worktree-<slug>`` convention — the main checkout and any
detached/foreign worktrees are skipped (the same filter as `scripts/wt.py`).

``ahead``/``behind`` are commit counts vs local ``main`` (the land target):
``ahead`` = commits on the branch not yet on main, ``behind`` = commits on main
not yet on the branch. A land is a fast-forward, so it succeeds only when
``behind == 0``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .const import BRANCH_PREFIX, WORKTREE_DIR, die
from .repo import base_ref, git_out, in_worktree, main_root


@dataclass
class Worktree:
    slug: str
    branch: str
    path: Path  # absolute
    ahead: int
    behind: int

    @property
    def fast_forwardable(self) -> bool:
        return self.behind == 0 and self.ahead > 0

    def status_note(self) -> str:
        if self.ahead == 0 and self.behind == 0:
            return "up to date with main — nothing to land"
        if self.behind > 0:
            return "needs rebase — NOT fast-forwardable"
        return "ready to land"

    def display(self) -> str:
        return (
            f"{self.slug:<28} {self.branch:<36} "
            f"+{self.ahead}/-{self.behind}  ({self.status_note()})"
        )


def ahead_behind(root: Path, branch: str) -> tuple[int, int]:
    """(ahead, behind) of ``branch`` vs the base ref. ``rev-list --left-right
    --count main...branch`` prints ``<left>\\t<right>`` = (main-only, branch-only)
    = (behind, ahead)."""
    out = git_out(
        ["-C", str(root), "rev-list", "--left-right", "--count", f"{base_ref()}...{branch}"],
        check=False,
    )
    parts = out.split()
    if len(parts) != 2:
        return (0, 0)
    behind, ahead = int(parts[0]), int(parts[1])
    return (ahead, behind)


def list_worktrees(root: Path) -> list[Worktree]:
    """All linked worktrees under ``.claude/worktrees/`` with a ``worktree-<slug>``
    branch, parsed from ``git worktree list --porcelain``."""
    worktrees: list[Worktree] = []
    block: dict[str, str] = {}

    def flush() -> None:
        path = block.get("worktree")
        ref = block.get("branch", "")
        if not path or not ref.startswith(f"refs/heads/{BRANCH_PREFIX}"):
            return
        norm = path.replace("\\", "/")
        marker = f"/{WORKTREE_DIR}/"
        if marker not in norm + "/":
            return
        branch = ref[len("refs/heads/") :]
        slug = branch[len(BRANCH_PREFIX) :]
        ahead, behind = ahead_behind(root, branch)
        worktrees.append(Worktree(slug, branch, Path(path), ahead, behind))

    for line in git_out(["-C", str(root), "worktree", "list", "--porcelain"]).splitlines():
        if not line:
            flush()
            block = {}
            continue
        key, _, value = line.partition(" ")
        block[key] = value
    flush()
    worktrees.sort(key=lambda w: w.slug)
    return worktrees


def select_worktree(
    worktrees: list[Worktree],
    target: str | None,
    *,
    prompt: str = "Select worktree",
) -> Worktree | None:
    """Pick a worktree by exact slug/branch/substring, or interactively via fzf.
    Returns None on cancel or no match."""
    if not target:
        return _fzf_select(worktrees, prompt)
    for wt in worktrees:
        if target == wt.slug or target == wt.branch or target in wt.slug or target in wt.branch:
            return wt
    return None


def _fzf_select(worktrees: list[Worktree], prompt: str) -> Worktree | None:
    """Interactive fuzzy-search selection via fzf; falls back to a numbered menu
    when fzf is absent. Returns None on cancel."""
    lines = [wt.display() for wt in worktrees]
    try:
        proc = subprocess.run(
            ["fzf", f"--prompt={prompt}> "],
            input="\n".join(lines).encode(),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return _numbered_select(worktrees, prompt)
    if proc.returncode != 0:
        return None
    selected = proc.stdout.decode().strip()
    for wt, line in zip(worktrees, lines):
        if line == selected:
            return wt
    return None


def _numbered_select(worktrees: list[Worktree], prompt: str) -> Worktree | None:
    print(f"{prompt}:")
    for i, wt in enumerate(worktrees, 1):
        print(f"  {i}) {wt.display()}")
    try:
        choice = input("Enter number: ").strip()
        idx = int(choice) - 1
        return worktrees[idx] if 0 <= idx < len(worktrees) else None
    except (ValueError, EOFError, KeyboardInterrupt):
        return None


def resolve_worktree(target: str | None, verb: str) -> Path:
    """Absolute path of the worktree a verb should act on.

    Inside a worktree → that worktree (``target`` ignored; you are already in it).
    On the main checkout → the slug in ``target`` if given, else an interactive
    picker. Dies when there are no worktrees, the slug is unknown, or the picker
    is cancelled."""
    if in_worktree():
        return Path(git_out(["rev-parse", "--show-toplevel"]))
    root = main_root()
    worktrees = list_worktrees(root)
    if not worktrees:
        die(f"no worktrees under {WORKTREE_DIR}/ to {verb}")
    if target:
        match = next((w for w in worktrees if w.slug == target), None)
        if match is None:
            avail = ", ".join(w.slug for w in worktrees)
            die(f"no worktree '{target}'. Available: {avail}")
        return match.path
    selected = select_worktree(worktrees, None, prompt=f"Select a worktree to {verb}")
    if selected is None:
        die("cancelled — no worktree selected")
    return selected.path


def run_list() -> None:
    if in_worktree():
        die("run 'wt list' from the main checkout, not from inside a worktree")
    worktrees = list_worktrees(main_root())
    if not worktrees:
        print("wt: no worktrees under .claude/worktrees/")
        return
    print(f"{'SLUG':<28} {'BRANCH':<36} +AHEAD/-BEHIND  STATUS")
    for wt in worktrees:
        print(wt.display())
