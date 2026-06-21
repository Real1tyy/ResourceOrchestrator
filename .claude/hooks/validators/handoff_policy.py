#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Hand-off harness policy for the Stop hook.

The close-of-loop analogue of the default-deny PreToolUse policy: instruct via
prompts, **verify via hooks**. Before an agent's session is allowed to Stop, the
worktree branch must be:

  1. **rebased onto local `main`** — 0 commits behind (main is an ancestor of HEAD),
     the same ahead/behind computation the worktree lifecycle uses
     (scripts/wt.py ``_ahead_behind``), and
  2. **pre-stop clean** — `mise run pre-stop` must exit 0. `pre-stop` is the heavy
     hand-off gate (change-report + all check gates + changed-spec e2e); downstream
     repos fill in the `check:*`/`test:*` bodies it delegates to. The stop hook runs
     it automatically so the agent doesn't have to remember — but agents can run it
     manually to validate before stopping.

The gate is invoked by the Claude Code **`Stop` hook** and gates the stop, so it is a
*pre-stop* check despite firing at session end. There is no `PreStop`/`PostStop` hook
event — `pre-stop` is the *task* name; the *invocation* is the `Stop` hook
([[decision-handoff-gate-pre-stop-rename]]).

Split into a PURE decision (:func:`decide`, given behind-count + pre-stop result)
and IMPURE resolvers (git behind-count, mise run) so the decision is unit-testable
without standing up a repo.

Posture (mirrors default-deny):

* **Block** on a resolved unmet condition — exit non-zero with a message naming the
  precise missing step and the command to fix it.
* **Fail open** when the state cannot be resolved (not a worktree, unresolvable git,
  mise not found). A harness bug must degrade enforcement, never trap a session.
* **Human escape:** ``AGENTIC_ALLOW_UNVERIFIED_HANDOFF=1`` bypasses the check, the
  same deliberate-override posture as the default-deny ``AGENTIC_ALLOW_MAIN_*`` hatches.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The worktree workflow's real-work location.
# Only a session running inside a worktree has a hand-off to gate.
WORKTREE_MARKER = ".claude/worktrees/"

# Deliberate-override hatch, mirroring the default-deny AGENTIC_ALLOW_MAIN_* hatches.
ENV_HATCH = "AGENTIC_ALLOW_UNVERIFIED_HANDOFF"

# The land target every worktree rebases onto (LOCAL main — origin is often stale).
BASE_REF = "main"

_OVERRIDE = f"(Human override for a deliberate hand-off: {ENV_HATCH}=1.)"


@dataclass(frozen=True)
class Decision:
    """Allow (``ok``) or block; ``message`` is the agent-facing reason on a block."""

    ok: bool
    message: str = ""


def _rebase_message(behind: int) -> str:
    plural = "" if behind == 1 else "s"
    return (
        f"BLOCKED (hand-off harness): branch is {behind} commit{plural} behind "
        f"{BASE_REF}. Rebase the branch onto {BASE_REF} before hand-off: "
        f"`mise run wt:rebase`. {_OVERRIDE}"
    )


def _pre_stop_message(returncode: int, head: str | None = None) -> str:
    sha = f" at HEAD {head[:12]}" if head else ""
    return (
        f"BLOCKED (hand-off harness): `mise run pre-stop` exited {returncode}{sha}. "
        "Fix the failing checks, then stop again (the hook will re-run it). "
        f"Run `mise run pre-stop` manually to iterate. {_OVERRIDE}"
    )


def decide(behind: int, pre_stop_ok: bool, pre_stop_code: int = 1) -> Decision:
    """The pure hand-off decision: rebased onto main AND pre-stop clean.

    Rebase is checked first so the message names the *first* missing step (a behind
    branch must rebase before its gate is even meaningful — the rebase moves HEAD).
    """
    if behind > 0:
        return Decision(False, _rebase_message(behind))
    if not pre_stop_ok:
        return Decision(False, _pre_stop_message(pre_stop_code))
    return Decision(True)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def behind_main(cwd: str, base: str = BASE_REF) -> int | None:
    """Commits ``base`` is ahead of HEAD — the branch's *behind* count, or ``None``.

    ``git rev-list --left-right --count <base>...HEAD`` prints ``<behind>\\t<ahead>``
    (left = base-only, right = HEAD-only). ``None`` on any failure (no ``base`` ref,
    detached HEAD) so the caller can fail open.
    """
    result = _git(["rev-list", "--left-right", "--count", f"{base}...HEAD"], cwd)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    try:
        return int(parts[0])
    except (IndexError, ValueError):
        return None


def head_sha(cwd: str) -> str | None:
    """The worktree's HEAD commit sha, or ``None`` when it can't be resolved."""
    result = _git(["rev-parse", "HEAD"], cwd)
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def green_pre_stop_shas(checkout: Path) -> set[str]:
    """The set of HEAD shas with a recorded green ``pre-stop`` run, read from gate
    telemetry.

    Scans ``<checkout>/.agentic/telemetry/<session>/<day>/gate_run.jsonl`` and
    collects ``commit_sha`` from every ``gate_type: pre-stop`` + ``status: pass``
    record. Sha-keyed, so a stale run keyed to an older sha never matches the current
    HEAD. Robust to malformed lines and unreadable files — a telemetry problem must
    never trap a hand-off.

    Not currently called by ``evaluate()``: the content-addressed gate result-cache
    (spec-gate-result-cache) already replays a green ``pre-stop`` in ~0 s on an
    unchanged tree, so a per-Stop re-run is cheap without a sha-keyed skip. This
    helper is retained for consumers that prove a green hand-off off the telemetry
    trace (e.g. the daemon's worktree stage).
    """
    shas: set[str] = set()
    root = checkout / ".agentic" / "telemetry"
    if not root.is_dir():
        return shas
    for path in root.glob("*/*/gate_run.jsonl"):
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    payload = record.get("payload") or {}
                    if (
                        payload.get("gate_type") == "pre-stop"
                        and payload.get("status") == "passed"
                        and payload.get("commit_sha")
                    ):
                        shas.add(payload["commit_sha"])
        except OSError:
            continue
    return shas


def run_pre_stop(cwd: str) -> tuple[bool, int]:
    """Run ``mise run pre-stop`` in the worktree. Returns ``(ok, returncode)``."""
    result = subprocess.run(
        ["mise", "run", "pre-stop"],
        cwd=cwd,
        check=False,
    )
    return result.returncode == 0, result.returncode


def is_worktree(cwd: str | None) -> bool:
    """True when ``cwd`` is inside a worktree under .claude/worktrees/."""
    return bool(cwd) and WORKTREE_MARKER in str(cwd).replace("\\", "/")


def hatch_armed() -> bool:
    """Whether the human override hatch is set (exact literal ``"1"``)."""
    return os.environ.get(ENV_HATCH) == "1"


def evaluate(cwd: str) -> Decision:
    """The full hand-off check for ``cwd``.

    Returns ``ok=True`` (allow Stop) when the check passes, is bypassed by the env
    hatch, runs outside a worktree, or cannot be evaluated (fail-open). Returns a
    blocking :class:`Decision` only on a *resolved* unmet condition.
    """
    if hatch_armed():
        return Decision(True)
    if not is_worktree(cwd):
        return Decision(True)
    behind = behind_main(cwd)
    if behind is None:
        # Unresolvable git state (no `main` ref, detached HEAD, not a repo) — fail
        # open rather than trap a session on a state the harness can't reason about.
        return Decision(True)
    if behind > 0:
        return Decision(False, _rebase_message(behind))
    sha = head_sha(cwd)
    ok, code = run_pre_stop(cwd)
    if not ok:
        return Decision(False, _pre_stop_message(code, sha))
    return Decision(True)
