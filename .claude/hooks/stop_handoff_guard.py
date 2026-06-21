#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Stop-hook hand-off harness guard.

Fires at session Stop. Blocks the stop (exit 2, message on stderr → shown to the
agent, which then continues) when the worktree branch is not rebased onto `main` or
`mise run pre-stop` exits non-zero, naming the exact missing step. The check itself
lives in ``validators/handoff_policy.py`` (pure decision + git resolvers).

There is no `PreStop` hook event; `pre-stop` is the *task* the `Stop` hook invokes —
a gate that gates the stop ([[decision-handoff-gate-pre-stop-rename]]).

Posture:
* **Bypass** when ``AGENTIC_ALLOW_UNVERIFIED_HANDOFF=1`` (a deliberate human override).
* **No-op** outside a worktree and on any unresolvable git state — a harness bug must
  degrade enforcement, never trap a session (fail-open).
* **One-shot nudge, never an infinite trap.** When ``stop_hook_active`` is set the
  session is already re-stopping *because* a prior Stop hook blocked it this cycle —
  the agent has been told what is missing. Re-blocking there would loop forever
  whenever the precondition is unsatisfiable from the agent's side (e.g. a `ci` left
  red by an orthogonal, not-this-worktree failure). So the guard blocks the *first*
  stop with its actionable message and lets a repeated stop through. This is the
  spec's own risk mitigation ("a legitimately-done agent wrongly blocked is worse than
  a missed warn") and the documented Claude Code loop-prevention pattern.
* **SubagentStop is deliberately NOT wired to this guard:** a subagent finishing is
  not the worktree hand-off boundary; the top-level session Stop is. Gating subagents
  would block ordinary delegation mid-loop.

The human's own terminal never triggers this — Stop hooks fire only inside a Claude
Code session, so "the human terminal is never gated" holds for free.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "validators"))

try:
    import handoff_policy
except ImportError:
    # Without the policy module there is nothing to enforce — never trap a stop.
    sys.exit(0)

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


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed/empty hook payload — fail open (allow the stop).
        sys.exit(0)

    # Already re-stopping after a prior block this cycle — deliver the nudge once,
    # never trap (see module docstring). Read before evaluating: cheap and decisive.
    if input_data.get("stop_hook_active"):
        sys.exit(0)

    # Repo-local PRE extension — before the built-in hand-off check (exit 2 to block).
    invoke_extension("stop_handoff_guard", "pre", input_data)

    try:
        cwd = input_data.get("cwd") or os.getcwd()
        decision = handoff_policy.evaluate(cwd)
        if not decision.ok:
            print(decision.message, file=sys.stderr)
            sys.exit(2)
        invoke_extension("stop_handoff_guard", "post", input_data)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — a guard bug must never trap a session
        fail_safe(exc, "stop_handoff_guard")


if __name__ == "__main__":
    main()
