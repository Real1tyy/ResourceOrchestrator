"""Repo-local telemetry-hook extension — SEEDED no-op, OWNED by this repo.

Seeded once by `agenticd init`; never overwritten thereafter. Put THIS repo's
machine-specific telemetry-event handling here (e.g. mirror/forward an event) instead
of editing the ported harness hook (`agentic_telemetry.py`)
([[decision-hook-extension-points]]).

`pre(data)` runs before the telemetry append; `post(data)` after it. Telemetry is
fail-safe and fire-and-forget — keep these phases side-effecting only; do not block.
`data` is the parsed hook payload.
"""


def pre(data):
    return


def post(data):
    return
