"""Repo-owned formatter for the PostToolUse auto-format hook — you fill this in.

SEED-ONCE + REPO-OWNED: `agenticd init` writes this stub only when it is absent and never
overwrites it (not even under `--force`), so your implementation always survives an
upgrade. It ships doing NOTHING — `format_file` is a no-op.

`agenticd` provides the *structure and opinion*: the bundle hook
`post_tool_use_format.py` calls `format_file(<path>)` with the ONE file Claude just
edited, after every Edit/Write/MultiEdit. Keeping each edited file formatted means your
`pre-commit` format gate is a confirmation, not a discovery — no commit→fmt-fail→retry
loop (efficiency finding 2026-06-17).

You provide the *formatting*. Two things to get right (see
[[decision-edit-time-auto-format-hook]]):
  * **Format ONLY `file_path`** — never the whole package. A whole-package `cargo fmt`
    would sweep unrelated unstaged edits into the change.
  * **Match your format gate byte-for-byte** (same tool/toolchain/edition), so the gate
    stays a no-op instead of fighting this hook.

Contract:
  * Format the single file at `file_path` in place. The return value is ignored.
  * NEVER raise to block: a syntax error mid-edit or a missing formatter must leave the
    file untouched, not wedge the edit. The hook swallows exceptions, but stay
    best-effort here too.

Example shape (Rust + web):

    # import subprocess
    # from pathlib import Path
    #
    # def format_file(file_path):
    #     ext = Path(file_path).suffix
    #     if ext == ".rs":
    #         subprocess.run(["rustfmt", file_path], check=False, timeout=30)
    #     elif ext in {".ts", ".tsx", ".json", ".css", ".md"}:
    #         subprocess.run(["npx", "prettier", "--write", file_path], check=False, timeout=30)
"""

from __future__ import annotations


def format_file(file_path: str) -> None:  # noqa: ARG001
    """Format the single file at ``file_path`` in place. No-op until the repo fills it in."""
