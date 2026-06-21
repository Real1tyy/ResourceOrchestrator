#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Append a Claude Code hook event to agenticd's on-disk telemetry trace.

Invoked by Claude Code via the wiring in ``.claude/settings.json`` as::

    uv run $CLAUDE_PROJECT_DIR/.claude/hooks/agentic_telemetry.py <hook_type>

It reads the hook payload from stdin (a JSON object Claude provides) and appends
one JSON line to::

    <checkout>/.agentic/telemetry/<session_id>/<YYYY-MM-DD>/<hook_type>.jsonl

This file is the on-disk source of truth the daemon ingests (spec 0034). The
hook is intentionally **telemetry-only** and **fail-safe**: any error exits 0 so
a telemetry hiccup never blocks a Claude session. Stdlib-only so ``uv`` can run
it with no dependency resolution.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from _extension import invoke_extension
except ImportError:  # dispatch helper absent → repo extensions disabled (no-op)

    def invoke_extension(stem, phase, data):  # noqa: ARG001
        return


def _resolve_checkout(payload: dict) -> Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    p = Path(base)
    # Worktrees follow <root>/.claude/worktrees/<name>; resolve to the root so
    # telemetry lands under <root>/.agentic/telemetry/ where the daemon watches.
    marker = "/.claude/worktrees/"
    s = str(p)
    idx = s.find(marker)
    if idx >= 0:
        return Path(s[:idx])
    return p


def _safe_segment(value: str, fallback: str) -> str:
    # Session ids are Claude-issued UUIDs, but never let a stray separator walk
    # the write outside the telemetry tree.
    cleaned = "".join(c for c in value if c.isalnum() or c in "-_.")
    return cleaned or fallback


def main() -> None:
    try:
        hook_type = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {"raw": payload}

        # Repo-local PRE extension — before the telemetry append (e.g. mirror/forward
        # the event). Telemetry stays fail-safe: a non-SystemExit error is swallowed.
        invoke_extension("agentic_telemetry", "pre", payload)

        session_id = _safe_segment(str(payload.get("session_id", "unknown")), "unknown")
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_dir = _resolve_checkout(payload) / ".agentic" / "telemetry" / session_id / day
        out_dir.mkdir(parents=True, exist_ok=True)

        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": hook_type,
                "session_id": payload.get("session_id", "unknown"),
                "cwd": payload.get("cwd"),
                "transcript_path": payload.get("transcript_path"),
                "source": payload.get("source"),
                "payload": payload,
            },
            ensure_ascii=False,
        )
        # Text-mode append maps to O_APPEND on POSIX, keeping each line atomic.
        with open(out_dir / f"{_safe_segment(hook_type, 'unknown')}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        # Repo-local POST extension — after the telemetry append.
        invoke_extension("agentic_telemetry", "post", payload)
    except Exception:
        # Telemetry must never break the session.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
