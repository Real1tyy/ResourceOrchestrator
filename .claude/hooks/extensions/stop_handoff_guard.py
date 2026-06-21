"""Repo-local Stop (hand-off guard) extension — SEEDED no-op, OWNED by this repo.

Seeded once by `agenticd init`; never overwritten thereafter. Put THIS repo's
machine-specific Stop checks here instead of editing the ported harness hook
(`stop_handoff_guard.py`) ([[decision-hook-extension-points]]).

`pre(data)` runs before the built-in hand-off check; `post(data)` after it passes. To
block the stop (nudge the agent to finish something), print a reason to stderr and
`sys.exit(2)`; returning is a pass. `data` is the parsed Stop payload.
"""


def pre(data):
    return


def post(data):
    return
