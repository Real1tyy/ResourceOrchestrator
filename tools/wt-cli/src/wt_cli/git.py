"""Git subprocess helpers + repo discovery.

Every git call in wt-cli goes through these wrappers so that:
  * `core.quotePath=false` is forced on (raw UTF-8 paths in diff/name-status
    output — see the docstring on `git()`).
  * Test stubs only have to patch one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def run(
    cmd: list[str],
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        input=input_bytes,
        capture_output=True,
        check=check,
    )


def git(
    args: list[str],
    cwd: Path,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a git command with `core.quotePath=false` forced on.

    `core.quotePath=true` (git's default) C-escapes any path byte outside
    7-bit ASCII when emitting diffs, name-status output, and similar text
    formats: `notes/starý.md` shows up as `"notes/star\\303\\275.md"`. Our
    rename/path-parsing regexes don't decode those escapes, so a Unicode
    `git mv` silently fails to match anything — the diff sails through the
    classifier as an unparseable block, and the worktree's intent is lost.

    Disabling it across the board guarantees raw UTF-8 paths in every
    diff-producing call (apply uses the patch we hand it; ls-files -z is
    already quoting-immune via its NUL terminator). This is the simplest
    place to fix the leak without sprinkling `-c` flags through every
    caller, and it has no observable effect on commands that don't print
    paths.
    """
    return run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        input_bytes=input_bytes,
        check=check,
    )


def git_text(args: list[str], cwd: Path) -> str:
    return git(args, cwd).stdout.decode("utf-8", errors="replace")


def repo_root() -> Path:
    """Return the absolute path of the repo containing the current cwd."""
    return Path(git_text(["rev-parse", "--show-toplevel"], Path.cwd()).strip())


def main_repo_of(wt_path: Path) -> Path | None:
    """Return the main repo root for any worktree (or the repo itself).

    For a linked worktree, `git rev-parse --git-common-dir` resolves to
    `<main-repo>/.git`; for the main repo itself, it resolves to `.git`.
    The parent of that path is always the main working tree.

    Returns None if the call fails (not a git repo, git unavailable).
    """
    try:
        raw = git_text(["rev-parse", "--git-common-dir"], wt_path).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not raw:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = (wt_path / common).resolve()
    return common.parent
