#!/usr/bin/env python3
"""guard_stale_staged.py — warn when a staged file's blob differs from the working tree.

The pre-commit gate runs ``cargo fmt --all -- --check`` (and the rest) against the
*staged* content, not the working tree. When a file is ``git add``-ed and then
edited again without re-staging, the index holds the OLD blob while disk holds the
new one. The gate keeps checking the stale staged copy, so it can flag a
``fmt --check`` diff for a file that is correctly formatted *on disk* — a confusing
failure whose real cause is a stale index, not a formatting bug. The classic
trigger: a failed commit leaves files staged, you reformat the working tree, then
try to commit a different chunk and chase a phantom rustfmt diff.

This guard surfaces that divergence at the commit boundary and names the fix
(re-``git add``). It is a WARNING, never a block: partial staging (``git add -p``)
is a legitimate workflow, so the guard always exits 0. Set
``AGENTIC_ALLOW_STALE_STAGED=1`` to silence it for a deliberate partial-staging
commit.

### Why this can't just read ``git diff``

lefthook **stashes unstaged changes** before running pre-commit commands (it saves
them to an auto-backup stash + ``.git/info/lefthook-unstaged.patch`` and checks the
index out to disk), so by the time this guard runs *inside the hook* the working
tree already equals the index and a live ``git diff`` shows nothing — exactly the
divergence we want to catch is hidden. So when the live diff is empty we read
lefthook's own backup patch to recover the set of files that had unstaged edits.
On a direct (non-lefthook) run there is no backup and the live diff is authoritative.
lefthook removes both artifacts after the commit, so a direct run never reads stale
data. If lefthook ever changes that storage the guard simply goes quiet (it only
ever warns), never a false block.

Thin-hook shape (ADR 0009): the logic lives here; lefthook's pre-commit stage just
invokes it via ``mise run guard:stale-staged``. Sibling of
guard_commit_location.py and guard_route_tree.py. See spec
gate-hygiene-fmt-pin-and-stale-staged-guard.

Stdlib only — no dependencies.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Depth-independent shared-lib import (spec-scripts-shared-lib): walk up to the
# scripts root (the dir holding lib/) so this resolves whether the script sits at
# scripts/foo.py or scripts/<sub>/foo.py.
for _ancestor in Path(__file__).resolve().parents:
    if (_ancestor / "lib" / "__init__.py").is_file():
        sys.path.insert(0, str(_ancestor))
        break
import lib  # noqa: E402

# lefthook saves the unstaged changes it stashes here (relative to the git dir).
LEFTHOOK_PATCH = "info/lefthook-unstaged.patch"


def _git(*args: str) -> str | None:
    result = lib.git(list(args), check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _names(output: str | None) -> set[str]:
    return {line.strip() for line in (output or "").splitlines() if line.strip()}


def paths_from_patch(text: str) -> set[str]:
    """Destination paths from a unified-diff ``diff --git a/… b/…`` patch — the
    files lefthook backed up because they had unstaged edits."""
    paths: set[str] = set()
    for line in text.splitlines():
        if line.startswith("diff --git ") and " b/" in line:
            paths.add(line.split(" b/", 1)[1].strip())
    return paths


def _lefthook_backup_paths() -> set[str]:
    """Files lefthook stashed as unstaged before running the hook. Empty when not
    running under lefthook (or if its backup storage moved)."""
    git_path = _git("rev-parse", "--git-path", LEFTHOOK_PATCH)
    if git_path:
        patch = Path(git_path.strip())
        if patch.is_file():
            return paths_from_patch(patch.read_text())
    return set()


def unstaged_paths() -> set[str]:
    """Paths whose working-tree blob differs from the index. Reads the live diff
    first; only when it is empty (lefthook stashed unstaged changes out of the way)
    does it fall back to lefthook's backup patch."""
    live = _names(_git("diff", "--name-only"))
    return live if live else _lefthook_backup_paths()


def main() -> int:
    if os.environ.get("AGENTIC_ALLOW_STALE_STAGED") == "1":
        return 0

    staged = _names(_git("diff", "--cached", "--name-only"))
    if not staged:
        return 0

    stale = sorted(staged & unstaged_paths())
    if not stale:
        return 0

    files = "\n".join(f"      {p}" for p in stale)
    add_target = stale[0] if len(stale) == 1 else "<the files above>"
    print(
        f"""guard-stale-staged: WARNING — staged content differs from the working tree.

  These files are staged AND have later unstaged edits, so their staged (indexed)
  blob is NOT what is on disk:
{files}

  The commit gate (`cargo fmt --check`, etc.) checks the staged content, not your
  working tree — so it may flag a diff for a file that looks correct on disk. If
  the gate fails on one of these with a phantom formatting diff, re-stage and
  recommit:

      git add {add_target}

  This is only a heads-up; the commit is NOT blocked (partial staging is allowed).
  Silence for an intentional partial-staging commit: AGENTIC_ALLOW_STALE_STAGED=1.""",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
