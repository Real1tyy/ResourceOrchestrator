"""Repo topology + string-returning git helpers for the lifecycle commands.

`git.py` is the low-level subprocess layer (bytes, `CompletedProcess`). The
lifecycle code wants stripped strings and a fail-with-guidance error, so this
module wraps it: `git_out` (capture stdout) and `git_stream` (let git write
straight to the terminal). Topology helpers (`main_root`, `base_ref`,
`in_worktree`) answer "where am I and what do I rebase onto".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .const import WORKTREE_DIR, WtError, die
from .git import run


def _cwd(cwd: Path | str | None) -> Path | None:
    if cwd is None:
        return None
    return Path(cwd)


def git_out(args: list[str], cwd: Path | str | None = None, *, check: bool = True) -> str:
    """Run a git command, returning stripped stdout. Raises `WtError` (after
    surfacing git's stderr) when ``check`` and the command fails."""
    proc = run(["git", *args], cwd=_cwd(cwd), check=False)
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").rstrip()
        if err:
            print(err, file=sys.stderr)
        die(f"git {' '.join(args)} failed (exit {proc.returncode})")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def git_stream(args: list[str], cwd: Path | str | None = None, *, check: bool = True) -> int:
    """Run a git command letting it write straight to this process's stdout/stderr
    (used for `worktree add`, `log --graph`, etc. where live output is the point).
    Returns the exit code; raises `WtError` when ``check`` and it fails."""
    proc = subprocess.run(["git", *args], cwd=str(_cwd(cwd)) if cwd else None, check=False)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} failed (exit {proc.returncode})")
    return proc.returncode


def main_root() -> Path:
    """Absolute path of the MAIN working tree (first entry of `git worktree
    list`), regardless of whether we are currently inside a linked worktree."""
    for line in git_out(["worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree ") :])
    die("could not determine the main worktree path")


def base_ref() -> str:
    """The ref a branch is rebased onto. This repo is LOCAL-FIRST
    (decision-worktree-baseref-head, worktree.baseRef = head): local ``main`` is
    the source of truth and is not necessarily pushed, so ``origin/main`` is
    often stale. We rebase onto LOCAL ``main`` — the ref the human's land
    fast-forwards (decision-worktree-rebase-model)."""
    return "main"


def in_worktree(cwd: Path | str | None = None) -> bool:
    """True when CWD is a worktree under .claude/worktrees/."""
    toplevel = git_out(["rev-parse", "--show-toplevel"], cwd)
    return f"/{WORKTREE_DIR}/" in toplevel.replace("\\", "/") + "/"


def behind_main_count(cwd: Path | str | None = None) -> int:
    """Commits on ``base_ref()`` not yet on HEAD — how far the branch's base
    trails ``main``. Best-effort: any git failure returns 0 so the advisory
    never fires spuriously and never raises."""
    out = git_out(["rev-list", "--count", f"HEAD..{base_ref()}"], cwd, check=False)
    try:
        return int(out.strip())
    except (ValueError, AttributeError):
        return 0


def warn_if_behind_main(cwd: Path | str | None = None) -> None:
    """Warn (non-fatal) when the branch's base trails ``main`` — the stale-base
    trap: a gate then compiles against an out-of-date workspace and fails in code
    the diff never touched (a sibling's already-landed fix is missing). Naming it
    up front turns a confusing unrelated-crate failure into "rebase first"."""
    behind = behind_main_count(cwd)
    if behind > 0:
        print(
            f"wt: ⚠ base is {behind} commit(s) behind {base_ref()} — rebase before gating "
            f"(`wt rebase`). A stale base can fail the gate in code your diff never "
            f"touched (a fix already on {base_ref()} is missing from your tree).",
            file=sys.stderr,
        )


__all__ = [
    "WtError",
    "base_ref",
    "behind_main_count",
    "die",
    "git_out",
    "git_stream",
    "in_worktree",
    "main_root",
    "warn_if_behind_main",
]
