"""Repo-local worktree-bootstrap extension — SEEDED no-op, OWNED by this repo.

Seeded once by `agenticd init`; never overwritten thereafter. Put THIS repo's
machine-specific per-worktree setup here (extra bootstrap a fresh worktree needs)
instead of editing the ported harness hook (`worktree_bootstrap.py`)
([[decision-hook-extension-points]]).

`pre(data)` runs before the built-in dep bootstrap; `post(data)` after it. Best-effort
like the hook itself — do not block the session. `data` is the parsed PostToolUse
(EnterWorktree) payload.
"""


def pre(data):
    return


def post(data):
    return
