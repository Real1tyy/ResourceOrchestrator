"""`wt rebase [slug]` — rebase a worktree's branch onto local ``main``."""

from __future__ import annotations

from .const import die
from .hooks import run_hook
from .lock import clear_stale_index_lock
from .repo import base_ref, git_out, git_stream, main_root
from .worktree import resolve_worktree


def run_rebase(target: str | None) -> None:
    worktree_abs = resolve_worktree(target, "rebase")
    if git_out(["-C", str(worktree_abs), "status", "--porcelain"]):
        die("working tree is dirty — commit or stash before rebasing")

    # Self-heal a STALE index.lock left by an interrupted git op — the same age-gated
    # clear the land path does, so `wt rebase` recovers without a separate `wt unlock`.
    # A fresh lock (a possibly-live op) is left.
    clear_stale_index_lock(worktree_abs)

    base = base_ref()
    ctx = {"worktree": worktree_abs, "base": base, "main_root": main_root()}
    run_hook("rebase", "pre", repo=worktree_abs, ctx=ctx)

    print(f"wt: rebasing {worktree_abs} onto {base}")
    rc = git_stream(["-C", str(worktree_abs), "rebase", base], check=False)
    if rc == 0:
        git_stream(
            ["-C", str(worktree_abs), "log", "--oneline", "--decorate", "--graph", "--max-count=20"],
            check=False,
        )
        print(f"wt: rebase clean onto {base}")
        run_hook("rebase", "post", repo=worktree_abs, ctx=ctx)
        return

    die(
        f"rebase stopped on conflicts. Resolve each file keeping BOTH sides'\n"
        f"    intent (see the merge-worktree skill), then:\n"
        f"      git -C {worktree_abs} add <resolved-file>\n"
        f"      git -C {worktree_abs} rebase --continue      # repeat until done\n"
        f"      git -C {worktree_abs} rebase --abort         # only to start over\n"
        f"    Re-run the gate (inside the worktree) once the rebase finishes."
    )
