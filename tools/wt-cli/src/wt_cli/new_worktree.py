"""`wt new <slug>` — create an isolated worktree off HEAD, then bootstrap it.

Off HEAD (decision-worktree-baseref-head, worktree.baseRef = head): the new
worktree inherits the local HEAD, including the spec file and any in-progress
commits.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .const import BRANCH_PREFIX, SLUG_RE, WORKTREE_DIR, die
from .hooks import run_hook
from .repo import git_out, git_stream, in_worktree, main_root


def _bootstrap(worktree_abs: Path) -> None:
    """Make a freshly created worktree gate-ready: trust its mise config, then run
    ``wt:setup`` (install deps + format). A fresh worktree has no installed deps
    (baseRef=head), so the first commit's pre-commit gate would otherwise fail with
    'not found' errors. Best effort — a failure warns but does not undo the worktree."""
    print("wt: bootstrapping worktree (mise trust + mise run wt:setup)…")
    subprocess.run(["mise", "trust"], cwd=str(worktree_abs), check=False)
    result = subprocess.run(["mise", "run", "wt:setup"], cwd=str(worktree_abs), check=False)
    if result.returncode != 0:
        print(
            "wt: WARNING — bootstrap (mise run wt:setup) failed. The worktree exists, "
            "but run `mise run wt:setup` inside it before committing (the pre-commit "
            "gate needs deps installed).",
            file=sys.stderr,
        )
    else:
        print("wt: bootstrap complete — gate is runnable")


def run_new(slug: str | None) -> None:
    if not slug:
        die("usage: wt new <slug>")
    if not SLUG_RE.match(slug):
        die(f"slug must be lowercase letters, digits, and single hyphens: '{slug}'")
    if in_worktree():
        die("run 'wt new' from the main checkout, not from inside a worktree")

    root = main_root()
    rel = f"{WORKTREE_DIR}/{slug}"
    branch = f"{BRANCH_PREFIX}{slug}"
    worktree_abs = root / rel
    if worktree_abs.exists():
        die(f"worktree already exists: {rel}")

    ctx = {"slug": slug, "branch": branch, "worktree": worktree_abs, "main_root": root}
    run_hook("new", "pre", repo=root, ctx=ctx)

    git_stream(["-C", str(root), "worktree", "add", rel, "-b", branch, "HEAD"])
    short = git_out(["-C", str(root), "rev-parse", "--short", "HEAD"])
    print(f"wt: created {rel} (branch {branch}) off {short}")

    _bootstrap(worktree_abs)
    run_hook("new", "post", repo=root, ctx=ctx)
    print(f"wt: cd {worktree_abs} to work in it")
