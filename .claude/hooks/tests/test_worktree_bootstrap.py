#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Unit tests for the EnterWorktree dep-bootstrap hook (spec-enterworktree-bootstrap-parity).

Two layers, mirroring test_command_policy.py:
  * ``should_bootstrap`` directly — the pure decision (no `mise` spawned).
  * the ``worktree_bootstrap.py`` hook end-to-end — feed it PostToolUse JSON on stdin and
    assert it exits 0 on the safe paths that never shell out (non-EnterWorktree, non-worktree
    cwd, malformed stdin).

Run with: ``mise run test:hooks`` (or ``python3 .claude/hooks/tests/test_worktree_bootstrap.py``).
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import worktree_bootstrap  # noqa: E402  (importing runs no main)

HOOK = HOOKS_DIR / "worktree_bootstrap.py"
WT = "/home/u/proj/.claude/worktrees/feat-x"
MAIN = "/home/u/proj"


def run_hook(payload):
    """Run the hook binary with `payload` on stdin; return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
    )


class ShouldBootstrap(unittest.TestCase):
    def test_enterworktree_js_worktree_without_node_modules_bootstraps(self):
        self.assertTrue(worktree_bootstrap.should_bootstrap("EnterWorktree", WT, True, False))

    def test_present_node_modules_is_noop(self):
        # Idempotent: re-entering an already-bootstrapped worktree does nothing.
        self.assertFalse(worktree_bootstrap.should_bootstrap("EnterWorktree", WT, True, True))

    def test_non_js_worktree_is_noop(self):
        # No package.json ⇒ nothing a per-worktree setup would install ⇒ skip (portable).
        self.assertFalse(worktree_bootstrap.should_bootstrap("EnterWorktree", WT, False, False))

    def test_non_worktree_cwd_never_bootstraps(self):
        # Never bootstrap the main checkout or an arbitrary directory.
        self.assertFalse(worktree_bootstrap.should_bootstrap("EnterWorktree", MAIN, True, False))

    def test_other_tool_never_bootstraps(self):
        self.assertFalse(worktree_bootstrap.should_bootstrap("Bash", WT, True, False))
        self.assertFalse(worktree_bootstrap.should_bootstrap("Edit", WT, True, False))

    def test_trailing_slash_worktree_cwd_bootstraps(self):
        self.assertTrue(worktree_bootstrap.should_bootstrap("EnterWorktree", WT + "/", True, False))


class HookEndToEnd(unittest.TestCase):
    def test_non_enterworktree_exits_zero_without_bootstrap(self):
        proc = run_hook({"tool_name": "Bash", "cwd": WT})
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("deps installed", proc.stdout)

    def test_enterworktree_non_worktree_cwd_exits_zero_without_bootstrap(self):
        # EnterWorktree but cwd is the main checkout → safe path, no `mise` spawn.
        proc = run_hook({"tool_name": "EnterWorktree", "cwd": MAIN})
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("deps installed", proc.stdout)

    def test_malformed_stdin_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
