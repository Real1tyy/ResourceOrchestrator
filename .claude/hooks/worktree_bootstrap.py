#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# ///

"""PostToolUse(EnterWorktree) → eager dep bootstrap, for parity with `wt:new`.

`wt:new` makes a worktree gate-ready on creation (scripts/wt.py::_bootstrap:
`mise trust` + the repo's setup task), so the first pre-commit gate has its deps.
The `EnterWorktree` harness tool does NOT — it stays in the same session and only
switches `cwd` into a fresh `baseRef=head` worktree. This hook closes that parity
gap: after EnterWorktree, when `cwd` is a `.claude/worktrees/` tree for a project
that needs a per-worktree dep install (a `package.json` with no `node_modules`),
it runs the same bootstrap once.

Portable + best-effort by design:
- Stack-agnostic: a worktree with no per-worktree deps (no `package.json`) is a
  no-op, so the hook ships in the bundle regardless of the downstream stack.
- Task-name-agnostic: the bundle convention is `wt:setup`; older installs use
  `setup`. The hook runs whichever the worktree actually defines, so one payload
  fits both without the bundle and a repo's mirror having to agree on the name.
- Never blocks or fails the session: a missing/failing/timed-out setup only warns.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from _hook_error import fail_safe
except ImportError:
    def fail_safe(exc: BaseException, hook_name: str) -> None:  # noqa: ARG001
        sys.exit(0)

try:
    from _extension import invoke_extension
except ImportError:  # dispatch helper absent → repo extensions disabled (no-op)
    def invoke_extension(stem, phase, data):  # noqa: ARG001
        return

# The only trees this hook touches (mirrors scripts/wt.py's WORKTREE_DIR).
WORKTREE_MARKER = "/.claude/worktrees/"

# Bundle convention is `wt:setup`; the dev repo (and older installs) use `setup`.
# Run the first one the worktree defines so a single payload fits both.
SETUP_TASK_CANDIDATES = ("wt:setup", "setup")

# A warm dep cache makes setup a seconds-long no-op; this ceiling only stops a
# pathological cold install from blocking the session indefinitely.
SETUP_TIMEOUT_SECONDS = 600


def should_bootstrap(
    tool_name: str, cwd: str, has_package_json: bool, node_modules_exists: bool
) -> bool:
    """True iff this PostToolUse event is an EnterWorktree into a `.claude/worktrees/`
    tree for a JS project (`package.json`) that has no `node_modules` yet. Pure (no
    I/O) so the decision is unit-tested without spawning `mise`. A non-JS worktree
    has nothing a per-worktree setup would install, so it is intentionally skipped."""
    if tool_name != "EnterWorktree":
        return False
    if WORKTREE_MARKER not in cwd.replace("\\", "/").rstrip("/") + "/":
        return False
    if not has_package_json:
        return False
    return not node_modules_exists


def available_tasks(cwd: str) -> set:
    """Mise task names defined for `cwd` (empty set on any failure). First token of
    each output line — task names never contain spaces, so a header/description
    column can't be mistaken for one of SETUP_TASK_CANDIDATES."""
    for argv in (["mise", "tasks", "ls"], ["mise", "tasks"]):
        try:
            proc = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        if proc.returncode == 0:
            return {line.split()[0] for line in proc.stdout.splitlines() if line.split()}
    return set()


def bootstrap(cwd: str) -> None:
    """`mise trust` + the worktree's setup task in `cwd`, mirroring wt.py::_bootstrap.
    Runs the first of SETUP_TASK_CANDIDATES the worktree defines. Best-effort: an
    absent / non-zero / timed-out task warns but the caller still exits 0."""
    subprocess.run(["mise", "trust"], cwd=cwd, check=False, capture_output=True)
    tasks = available_tasks(cwd)
    task = next((t for t in SETUP_TASK_CANDIDATES if t in tasks), None)
    if task is None:
        return
    try:
        result = subprocess.run(
            ["mise", "run", task],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=SETUP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"worktree_bootstrap: `mise run {task}` timed out in {cwd}; "
            "run it manually before committing (the pre-commit gate needs deps)."
        )
        return
    if result.returncode != 0:
        print(
            f"worktree_bootstrap: `mise run {task}` failed in {cwd}; "
            "run it manually before committing (the pre-commit gate needs deps)."
        )
        for line in (result.stderr or result.stdout or "").strip().splitlines()[-3:]:
            print(f"  {line}")
    else:
        print(f"worktree_bootstrap: deps installed in {cwd} via `mise run {task}` (parity with wt:new)")


def main():
    try:
        input_data = json.load(sys.stdin)
        invoke_extension("worktree_bootstrap", "pre", input_data)
        tool_name = input_data.get("tool_name", "")
        cwd = input_data.get("cwd") or os.getcwd()
        has_package_json = (Path(cwd) / "package.json").is_file()
        node_modules_exists = (Path(cwd) / "node_modules").exists()
        if should_bootstrap(tool_name, cwd, has_package_json, node_modules_exists):
            bootstrap(cwd)
        invoke_extension("worktree_bootstrap", "post", input_data)
        sys.exit(0)
    except json.JSONDecodeError as exc:
        fail_safe(exc, "worktree_bootstrap")
    except Exception as exc:
        fail_safe(exc, "worktree_bootstrap")


if __name__ == "__main__":
    main()
