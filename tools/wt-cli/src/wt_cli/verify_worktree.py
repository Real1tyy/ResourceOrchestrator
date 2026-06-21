"""`wt verify` / `wt warn-behind` — self-review surfaces.

These are read-only review aids: a diff-vs-merge-base summary and the non-fatal
stale-base advisory the gate runs as a preflight.
"""

from __future__ import annotations

import re
import subprocess

from .repo import base_ref, git_out, git_stream, warn_if_behind_main
from .worktree import resolve_worktree


def run_verify(target: str | None) -> None:
    worktree_abs = resolve_worktree(target, "verify")
    warn_if_behind_main(worktree_abs)

    base = base_ref()
    merge_base = git_out(
        ["-C", str(worktree_abs), "merge-base", "HEAD", base], check=False
    ) or git_out(["-C", str(worktree_abs), "rev-parse", "HEAD"])

    print(f"=== wt verify — self-review inputs ({worktree_abs} vs {base}) ===")
    print("--- committed + working-tree changes since merge-base ---")
    git_stream(["-C", str(worktree_abs), "--no-pager", "diff", "--stat", merge_base], check=False)
    print("\n--- untracked files ---")
    print(git_out(["-C", str(worktree_abs), "ls-files", "--others", "--exclude-standard"]))

    # The change-report tool is not wired yet. When the `report` task lands, prefer it;
    # until then this diff summary is the review surface.
    tasks = subprocess.run(["mise", "tasks"], capture_output=True, text=True, check=False)
    if re.search(r"(?m)^\s*report\b", tasks.stdout):
        print("\nwt: change-report tool detected — run 'mise run report' for the full lens")
    else:
        print(
            "\nwt: change-report tool not wired yet — review the diff above against "
            "the spec's acceptance criteria"
        )


def run_warn_behind() -> None:
    """Print the stale-base warning if the branch's base trails main, else nothing.
    Always succeeds — a non-fatal advisory the gate runs as a preflight."""
    warn_if_behind_main()
