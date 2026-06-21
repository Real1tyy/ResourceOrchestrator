"""Repo-owned formatter for the PostToolUse auto-format hook.

Formats the ONE file Claude just edited, in place, so the `format:check` gate is a
confirmation rather than a discovery. Must match the format gates byte-for-byte
(rustfmt via the pinned toolchain; prettier via the frontend's local install) and must
never raise — a missing formatter or a mid-edit syntax error leaves the file untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_PRETTIER_EXTS = {".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html", ".md", ".yaml", ".yml"}
_WEB_DIR = Path("apps/web")


def format_file(file_path: str) -> None:
    """Format the single file at ``file_path`` in place. Best-effort, never raises."""
    try:
        path = Path(file_path)
        ext = path.suffix
        if ext == ".rs":
            subprocess.run(
                ["rustfmt", "--edition", "2021", str(path)],
                check=False,
                timeout=30,
                capture_output=True,
            )
        elif ext in _PRETTIER_EXTS:
            # Use the frontend's pinned prettier so formatting matches `format:check`.
            subprocess.run(
                ["pnpm", "-C", str(_WEB_DIR), "exec", "prettier", "--write", str(path.resolve())],
                check=False,
                timeout=60,
                capture_output=True,
            )
    except Exception:
        # A formatter hiccup must never wedge the edit.
        return
