#!/usr/bin/env python3

"""Tests for the bundle PreToolUse guard: no file edits while a commit gate runs.

The guard ships in the bundle hook (`pre_tool_use.py`) so every agenticd-managed repo
gets it. Detection = lefthook's stash patch (`<common-gitdir>/info/lefthook-unstaged.patch`,
present only between stash and pop) AND a live commit/lefthook process whose cwd is inside
the edit target's checkout. The process check makes it stale-safe (a crashed commit's
leftover patch never bricks edits) and per-worktree scoped (the patch path is shared across
a repo's worktrees). The /proc scan is stubbed for determinism.

Run via `mise run test:hooks`.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "pre_tool_use.py"
_spec = importlib.util.spec_from_file_location("bundle_pre_tool_use", HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=_GIT_ENV)


class TestPureDecision(unittest.TestCase):
    """The IO-free core: process classification, containment, the in-flight rule."""

    def test_is_commit_proc(self):
        self.assertTrue(hook._is_commit_proc("lefthook run pre-commit"))
        self.assertTrue(hook._is_commit_proc("/usr/bin/git commit -m wip"))
        self.assertFalse(hook._is_commit_proc("git status"))
        self.assertFalse(hook._is_commit_proc("nvim src/a.rs"))

    def test_within(self):
        self.assertTrue(hook._within("/a/b/c", "/a/b"))
        self.assertTrue(hook._within("/a/b", "/a/b"))
        self.assertFalse(hook._within("/a/x", "/a/b"))

    def test_common_gitdir(self):
        self.assertEqual(hook._common_gitdir("/r/.git"), "/r/.git")
        self.assertEqual(hook._common_gitdir("/r/.git/worktrees/wt"), "/r/.git")

    def test_gate_in_flight(self):
        self.assertFalse(hook._gate_in_flight("/wt", False, [("lefthook", "/wt")]))
        self.assertFalse(hook._gate_in_flight("/wt", True, []))  # stale patch, no live proc
        self.assertTrue(hook._gate_in_flight("/wt", True, [("lefthook run pre-commit", "/wt/s")]))
        self.assertFalse(hook._gate_in_flight("/wt", True, [("lefthook", "/other")]))  # sibling
        self.assertFalse(hook._gate_in_flight("/wt", True, [("nvim x", "/wt")]))  # not a commit


class TestEditDuringCommitBlocked(unittest.TestCase):
    """Drive `_edit_during_commit_blocked` against a real temp repo + patch file, /proc
    stubbed — exercises gitdir resolution, patch lookup, and worktree scoping for real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        os.environ.pop("AGENTIC_ALLOW_EDIT_DURING_COMMIT", None)
        _git(self.root, "init", "-q")
        (self.root / "src").mkdir()
        self.patch = self.root / ".git" / "info" / "lefthook-unstaged.patch"
        self.patch.parent.mkdir(parents=True, exist_ok=True)
        self._orig = hook._running_commit_procs
        self.addCleanup(lambda: setattr(hook, "_running_commit_procs", self._orig))

    def _stub(self, procs):
        hook._running_commit_procs = lambda proc_root="/proc": procs

    def _ti(self):
        return {"file_path": str(self.root / "src" / "a.rs")}

    def test_blocks_when_gate_live(self):
        self.patch.write_text("", encoding="utf-8")
        self._stub([("lefthook run pre-commit", str(self.root))])
        self.assertTrue(hook._edit_during_commit_blocked("Edit", self._ti(), str(self.root)))

    def test_no_patch_means_no_running_gate(self):
        self._stub([("lefthook run pre-commit", str(self.root))])
        self.assertFalse(hook._edit_during_commit_blocked("Edit", self._ti(), str(self.root)))

    def test_stale_patch_without_live_proc(self):
        self.patch.write_text("", encoding="utf-8")  # crashed commit left it behind
        self._stub([])
        self.assertFalse(hook._edit_during_commit_blocked("Edit", self._ti(), str(self.root)))

    def test_sibling_worktree_commit_does_not_block(self):
        self.patch.write_text("", encoding="utf-8")
        self._stub([("lefthook run pre-commit", "/some/other/worktree")])
        self.assertFalse(hook._edit_during_commit_blocked("Edit", self._ti(), str(self.root)))

    def test_env_override(self):
        self.patch.write_text("", encoding="utf-8")
        self._stub([("lefthook run pre-commit", str(self.root))])
        os.environ["AGENTIC_ALLOW_EDIT_DURING_COMMIT"] = "1"
        self.addCleanup(lambda: os.environ.pop("AGENTIC_ALLOW_EDIT_DURING_COMMIT", None))
        self.assertFalse(hook._edit_during_commit_blocked("Edit", self._ti(), str(self.root)))

    def test_non_write_tool_ignored(self):
        self.patch.write_text("", encoding="utf-8")
        self._stub([("lefthook run pre-commit", str(self.root))])
        self.assertFalse(hook._edit_during_commit_blocked("Read", {}, str(self.root)))


if __name__ == "__main__":
    unittest.main()
