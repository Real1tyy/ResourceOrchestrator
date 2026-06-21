"""Tiny console primitives used across wt-cli commands."""

from __future__ import annotations

import sys


def confirm(prompt: str) -> bool:
    """Yes/no prompt. False on EOF / non-TTY / anything other than y[es]."""
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")
