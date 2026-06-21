#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
# ]
# ///

"""
agenticd status line — the canonical, bundle-managed Claude Code status line.

Display:
  dir:branch | [Model] | eff:xhigh | # [###---] | 42.5% used | ~115k left | 5h 31% (2h14m) · 7d 58% (3d) | session

Over the prior context-only line this adds the two signals the maintainer wanted at a
glance: the live reasoning **effort level** (`effort.level`) and **plan-usage** against the
rolling rate limits (`rate_limits.five_hour|seven_day`). Every optional field fails soft —
effort is absent on models without the param, rate_limits only exist for subscribers after
the first API response, and context fields are null early in a session — so a segment whose
data is missing simply drops out and the line never crashes the PTY (always exits 0).

Payload schema: docs/knowledge/claude-statusline-payload.md.
"""

import json
import os
import re
import subprocess
import sys
import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv is optional — the script runs identically under bare python3.


# ANSI color codes
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BRIGHT_RED = "\033[91m"
DIM = "\033[90m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
RESET = "\033[0m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text):
    """Drop ANSI escapes — for width math and for tests asserting on plain content."""
    return _ANSI_RE.sub("", text)


def get_usage_color(percentage):
    """Escalating color for a 0–100 usage percentage."""
    if percentage < 50:
        return GREEN
    elif percentage < 75:
        return YELLOW
    elif percentage < 90:
        return RED
    else:
        return BRIGHT_RED


# Effort levels ordered low→high, each with an escalating color so a glance reads severity.
_EFFORT_COLORS = {
    "low": DIM,
    "medium": CYAN,
    "high": YELLOW,
    "xhigh": MAGENTA,
    "max": BRIGHT_RED,
}


def create_progress_bar(percentage, width=15):
    """Visual `[###---]` bar, colored by usage."""
    filled = int((percentage / 100) * width)
    empty = width - filled
    color = get_usage_color(percentage)
    bar = f"{color}{'#' * filled}{DIM}{'-' * empty}{RESET}"
    return f"[{bar}]"


def get_worktree_info(cwd):
    """Return (dir_basename, git_branch). Either can be empty string."""
    if not cwd:
        return "", ""
    dir_basename = os.path.basename(cwd.rstrip("/")) or ""
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        branch = ""
    return dir_basename, branch


def format_tokens(tokens):
    """Human-readable token count (1.2k, 3.40M)."""
    if tokens is None:
        return "0"
    if tokens < 1000:
        return str(int(tokens))
    elif tokens < 1000000:
        return f"{tokens / 1000:.1f}k"
    else:
        return f"{tokens / 1000000:.2f}M"


def format_duration(seconds):
    """Compact countdown to a rate-limit reset: at most two units (3d2h, 2h14m, 45m)."""
    seconds = int(seconds)
    if seconds <= 0:
        return "now"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m" if minutes else "<1m"


def render_effort(input_data):
    """`eff:<level>` colored by level; dimmed when extended thinking is off. None if absent."""
    effort = input_data.get("effort") or {}
    level = effort.get("level")
    if not level:
        return None
    color = _EFFORT_COLORS.get(level, CYAN)
    thinking = input_data.get("thinking") or {}
    # `enabled` defaults to "unknown" (None) — only an explicit False dims the segment.
    if thinking.get("enabled") is False:
        color = DIM
    return f"{color}eff:{level}{RESET}"


def _render_rate_window(window, label, now):
    """One rate-limit window as `5h 31% (2h14m)`. None when the window has no usage %."""
    if not isinstance(window, dict):
        return None
    pct = window.get("used_percentage")
    if pct is None:
        return None
    seg = f"{get_usage_color(pct)}{label} {pct:.0f}%{RESET}"
    resets_at = window.get("resets_at")
    if isinstance(resets_at, (int, float)):
        remaining = resets_at - now
        if remaining > 0:
            seg += f"{DIM} ({format_duration(remaining)}){RESET}"
    return seg


def render_rate_limits(input_data, now):
    """`5h NN% (…) · 7d NN% (…)`. None when no window has data (non-subscriber / pre-first-response)."""
    rate_limits = input_data.get("rate_limits") or {}
    windows = [
        _render_rate_window(rate_limits.get("five_hour"), "5h", now),
        _render_rate_window(rate_limits.get("seven_day"), "7d", now),
    ]
    present = [w for w in windows if w]
    if not present:
        return None
    return f"{DIM} · {RESET}".join(present)


def generate_status_line(input_data, now=None):
    """Build the full status line. `now` (epoch seconds) is injectable for deterministic tests."""
    if now is None:
        now = time.time()

    model_info = input_data.get("model", {}) or {}
    model_name = model_info.get("display_name", "Claude")

    session_id = input_data.get("session_id", "") or "--------"

    context_data = input_data.get("context_window", {}) or {}
    used_percentage = context_data.get("used_percentage", 0) or 0
    context_window_size = context_data.get("context_window_size", 200000) or 200000
    remaining_tokens = int(context_window_size * ((100 - used_percentage) / 100))
    usage_color = get_usage_color(used_percentage)

    workspace = input_data.get("workspace", {}) or {}
    cwd = workspace.get("current_dir") or input_data.get("cwd") or ""
    dir_name, branch = get_worktree_info(cwd)

    parts = []

    # Worktree directory and branch (leftmost — "where am I?")
    if dir_name and branch:
        parts.append(f"{GREEN}{dir_name}{DIM}:{RESET}{MAGENTA}{branch}{RESET}")
    elif dir_name:
        parts.append(f"{GREEN}{dir_name}{RESET}")
    elif branch:
        parts.append(f"{MAGENTA}{branch}{RESET}")

    parts.append(f"{CYAN}[{model_name}]{RESET}")

    effort_segment = render_effort(input_data)
    if effort_segment:
        parts.append(effort_segment)

    parts.append(f"{MAGENTA}#{RESET} {create_progress_bar(used_percentage)}")
    parts.append(f"{usage_color}{used_percentage:.1f}%{RESET} used")
    parts.append(f"{BLUE}~{format_tokens(remaining_tokens)} left{RESET}")

    rate_segment = render_rate_limits(input_data, now)
    if rate_segment:
        parts.append(rate_segment)

    parts.append(f"{DIM}{session_id}{RESET}")

    return " | ".join(parts)


def main():
    try:
        input_data = json.loads(sys.stdin.read())
        print(generate_status_line(input_data))
        sys.exit(0)
    except json.JSONDecodeError:
        print(f"{RED}[Claude] # Error: Invalid JSON{RESET}")
        sys.exit(0)
    except Exception as e:  # a status line must never crash the PTY
        print(f"{RED}[Claude] # Error: {str(e)}{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
