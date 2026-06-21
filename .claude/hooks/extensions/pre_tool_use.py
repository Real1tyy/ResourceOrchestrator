"""Repo-local PreToolUse extension — SEEDED no-op, OWNED by this repo.

Seeded once by `agenticd init`; never overwritten thereafter (init treats it as
repo-owned). Put THIS repo's machine-specific PreToolUse checks here instead of
editing the ported harness hook (`pre_tool_use.py`) — that keeps repo-specific policy
out of the agenticd bundle pushed to every other repo ([[decision-hook-extension-points]]).

`pre(data)` runs before the harness hook's built-in guards; `post(data)` after they
all pass. To block a tool call, print a reason to stderr and `sys.exit(2)`; returning
is a pass. `data` is the parsed hook JSON payload (`tool_name`, `tool_input`, `cwd`, …).
"""


def pre(data):
    return


def post(data):
    return
