"""`wt land [slug]` — the HUMAN's Stage-8 op: fast-forward main + tear down.

Deliberately kept OFF the agent allowlist — an agent's `wt land` is blocked by
the default-deny hook. Fast-forward ``main`` to the branch + tear down,
auto-rebasing the branch onto ``main`` first when the two have diverged.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .const import die
from .hooks import run_hook
from .lock import clear_stale_index_lock, is_index_lock_failure
from .repo import base_ref, git_out, git_stream, in_worktree, main_root
from .worktree import list_worktrees, select_worktree


def _fast_forward_main(root: Path, branch: str) -> bool:
    """Switch the main checkout to main and fast-forward it to ``branch``. Returns
    True on success. ``--ff-only`` aborts WITHOUT mutating main when the branch has
    diverged, so a False return leaves main untouched and the caller can rebase."""
    git_stream(["-C", str(root), "switch", "main"])
    return (
        subprocess.run(
            ["git", "-C", str(root), "merge", "--ff-only", branch], check=False
        ).returncode
        == 0
    )


def _rebase_branch_onto_main(worktree_abs: Path, slug: str) -> None:
    """Rebase a worktree's branch onto main from INSIDE that worktree. Dies with
    actionable guidance on a dirty tree or conflicts; in the conflict case the
    rebase is left in progress for in-worktree resolution, and main is never touched.

    A stale ``index.lock`` would otherwise block the rebase before it detaches HEAD,
    so we (1) clear a stale lock first (age-gated) and (2) classify a lock failure
    distinctly from a real conflict."""
    base = base_ref()
    if git_out(["-C", str(worktree_abs), "status", "--porcelain"]):
        die(
            f"the worktree for '{slug}' has uncommitted changes — commit or stash "
            f"inside it, then re-run 'wt land {slug}'"
        )
    clear_stale_index_lock(worktree_abs)
    print(f"wt: rebasing worktree-{slug} onto {base} (in {worktree_abs})…")
    # Capture output so a failure can be classified; re-emit it so the operator still
    # sees git's own messages.
    proc = subprocess.run(
        ["git", "-C", str(worktree_abs), "rebase", base],
        check=False,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        if is_index_lock_failure(proc.stdout + proc.stderr):
            die(
                f"rebase could NOT START — a git index.lock is still blocking it, so no\n"
                f"    commits moved and main is untouched (this is NOT a merge conflict).\n"
                f"    Clear the stale lock, then re-run the land:\n"
                f"      cd {worktree_abs} && wt unlock\n"
                f"      wt land {slug}"
            )
        die(
            f"rebase onto {base} hit conflicts — main is untouched. Resolve them inside\n"
            f"    the worktree keeping BOTH sides' intent (the merge-worktree skill):\n"
            f"      git -C {worktree_abs} status                       # conflicted files\n"
            f"      # edit each, then for every one:\n"
            f"      git -C {worktree_abs} add <file>\n"
            f"      git -C {worktree_abs} rebase --continue            # repeat until done\n"
            f"      git -C {worktree_abs} rebase --abort               # to bail out\n"
            f"    Re-green the gate (in the worktree), then re-run 'wt land {slug}'."
        )
    print(f"wt: rebase clean onto {base}")


def _teardown_worktree(root: Path, worktree_abs: Path, branch: str) -> None:
    """Remove the worktree and delete its now-merged branch (Stage-8 teardown).

    The branch is already fast-forwarded into main, so all COMMITTED work is safe.
    A bare ``git worktree remove`` refuses on a dirty tree, which would strand the
    human's land step on leftover files the agent cannot delete — so we force past a
    dirty tree, but first print exactly what is being discarded. The remove is made
    idempotent and non-fatal: a still-live in-worktree session can re-create
    gitignored files during git's recursive delete, tripping the final rmdir, so any
    surviving directory is swept and the dangling registration pruned. Teardown
    always converges on "dir gone, branch gone"."""
    dirty = git_out(["-C", str(worktree_abs), "status", "--porcelain"])
    if dirty:
        print(
            "wt: worktree has uncommitted changes (not on main) — discarding on teardown:",
            file=sys.stderr,
        )
        for line in dirty.splitlines():
            print(f"      {line}", file=sys.stderr)
        git_stream(
            ["-C", str(root), "worktree", "remove", "--force", str(worktree_abs)], check=False
        )
    else:
        git_stream(["-C", str(root), "worktree", "remove", str(worktree_abs)], check=False)
    if worktree_abs.exists():
        print(
            f"wt: worktree dir survived git removal (live writer?) — sweeping {worktree_abs}",
            file=sys.stderr,
        )
        shutil.rmtree(worktree_abs, ignore_errors=True)
    git_stream(["-C", str(root), "worktree", "prune"], check=False)
    git_stream(["-C", str(root), "branch", "-d", branch], check=False)


def run_land(target: str | None) -> None:
    if in_worktree():
        die("run 'wt land' from the main checkout, not from inside the worktree")

    root = main_root()
    worktrees = list_worktrees(root)
    if not worktrees:
        die("no worktrees under .claude/worktrees/ to land")

    if target:
        selected = next((w for w in worktrees if w.slug == target), None)
        if selected is None:
            avail = ", ".join(w.slug for w in worktrees)
            die(f"no worktree '{target}'. Available: {avail}")
    else:
        selected = select_worktree(worktrees, None, prompt="Select a worktree to land into main")
        if selected is None:
            die("cancelled — no worktree selected")

    branch = selected.branch
    worktree_abs = selected.path

    # The fast-forward mutates the MAIN checkout's index; clear a stale lock there
    # first (age-gated — a fresh lock is left, never raced).
    clear_stale_index_lock(root)

    ctx = {"slug": selected.slug, "branch": branch, "worktree": worktree_abs, "main_root": root}
    run_hook("land", "pre", repo=root, ctx=ctx)

    if not _fast_forward_main(root, branch):
        print("wt: fast-forward not possible (main moved) — rebasing the branch onto main first")
        _rebase_branch_onto_main(worktree_abs, selected.slug)
        if not _fast_forward_main(root, branch):
            die(
                f"still not fast-forwardable after rebasing worktree-{selected.slug} onto main — "
                f"inspect manually (git -C {root} log --oneline --graph main {branch})"
            )

    _teardown_worktree(root, worktree_abs, branch)
    run_hook("land", "post", repo=root, ctx=ctx)
    print(f"wt: landed {branch} into main and tore down {worktree_abs}")
