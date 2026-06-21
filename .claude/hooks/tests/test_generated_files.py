#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Tests for the generated-files edit-guard in pre_tool_use.py
(spec generated-files-manifest-and-edit-block).

Table-driven over ``_generated_file_edit_blocked``: match / no-match / negation /
missing-manifest fail-open / worktree-relative matching / override env / new-file
under a generated dir, plus one end-to-end through the hook asserting the block
message and exit 2.

Runs under a bare ``python3`` (no uv, no third-party install): importing
``pre_tool_use`` puts ``_vendor/`` on ``sys.path``, so its ``pathspec`` matcher
resolves from the VENDORED copy (spec-hook-dedep-pathspec). Production runs the
hook the same way — the inline metadata declares ``dependencies = []``.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR / "validators"))
sys.path.insert(0, str(HOOKS_DIR))

import pre_tool_use  # noqa: E402

HOOK = HOOKS_DIR / "pre_tool_use.py"

# A realistic manifest: a single-file pattern, two directory patterns, and a
# negation carving one file back out of a matched directory.
MANIFEST = (
    "# generated files\n"
    "openapi/agenticd.openapi.json\n"
    "apps/web/src/generated/\n"
    "crates/agenticd-db/src/entities/\n"
    "!crates/agenticd-db/src/entities/entity_relations.rs\n"
)


def _write_manifest(root: Path, text: str = MANIFEST) -> None:
    manifest = root / ".claude" / "hooks" / "policy" / "generated"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(text, encoding="utf-8")


def _blocked(root: Path, rel: str, *, tool: str = "Edit", key: str = "file_path") -> bool:
    """Run the guard for an edit of ``<root>/<rel>`` with cwd at the checkout root."""
    return pre_tool_use._generated_file_edit_blocked(
        tool, {key: str(root / rel)}, str(root)
    )


class TestGeneratedFileGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Each guard test owns the env var; keep it clear by default.
        os.environ.pop("AGENTIC_ALLOW_GENERATED_EDIT", None)

    def test_match_under_generated_dir(self):
        _write_manifest(self.root)
        self.assertTrue(_blocked(self.root, "apps/web/src/generated/api/x.ts"))

    def test_match_single_file_pattern(self):
        _write_manifest(self.root)
        self.assertTrue(_blocked(self.root, "openapi/agenticd.openapi.json"))

    def test_no_match_for_authored_file(self):
        _write_manifest(self.root)
        self.assertFalse(_blocked(self.root, "apps/web/src/app.tsx"))
        self.assertFalse(_blocked(self.root, "crates/agenticd-db/src/migrations/m1.rs"))

    def test_negation_unblocks_one_file_but_siblings_stay_blocked(self):
        _write_manifest(self.root)
        # The negated file is editable…
        self.assertFalse(
            _blocked(self.root, "crates/agenticd-db/src/entities/entity_relations.rs")
        )
        # …while a sibling under the same generated dir stays blocked.
        self.assertTrue(
            _blocked(self.root, "crates/agenticd-db/src/entities/users.rs")
        )

    def test_new_file_under_generated_dir_is_blocked(self):
        """Writing a *new* (non-existent) file under a generated dir is blocked —
        gitignore dir patterns match by prefix (resolved open question)."""
        _write_manifest(self.root)
        target = self.root / "apps/web/src/generated/api/brand_new.ts"
        self.assertFalse(target.exists())
        self.assertTrue(_blocked(self.root, "apps/web/src/generated/api/brand_new.ts"))

    def test_missing_manifest_blocks_nothing(self):
        # No .agentic/generated anywhere up the tree → fail open.
        self.assertFalse(_blocked(self.root, "apps/web/src/generated/api/x.ts"))

    def test_empty_manifest_blocks_nothing(self):
        _write_manifest(self.root, "# only comments\n\n")
        self.assertFalse(_blocked(self.root, "apps/web/src/generated/api/x.ts"))

    def test_override_env_disables_block(self):
        _write_manifest(self.root)
        os.environ["AGENTIC_ALLOW_GENERATED_EDIT"] = "1"
        self.assertFalse(_blocked(self.root, "apps/web/src/generated/api/x.ts"))

    def test_non_write_tool_is_unaffected(self):
        _write_manifest(self.root)
        self.assertFalse(_blocked(self.root, "apps/web/src/generated/api/x.ts", tool="Read"))

    def test_notebook_path_key_is_honored(self):
        _write_manifest(self.root, "notebooks/generated/\n")
        self.assertTrue(
            _blocked(self.root, "notebooks/generated/x.ipynb", tool="NotebookEdit", key="notebook_path")
        )

    def test_worktree_relative_matching(self):
        """A worktree carries its own committed manifest; the target is matched
        relative to the WORKTREE root, not the enclosing main checkout."""
        wt = self.root / ".claude" / "worktrees" / "wt-x"
        (wt).mkdir(parents=True, exist_ok=True)
        _write_manifest(wt)
        # The main checkout has a *different* manifest that would NOT match this
        # path — proving the walk stops at the nearest (worktree) manifest.
        _write_manifest(self.root, "some/other/path/\n")
        self.assertTrue(_blocked(wt, "apps/web/src/generated/api/x.ts"))


class TestGeneratedGuardEndToEnd(unittest.TestCase):
    """Drive the whole hook over stdin and assert the exit code + message."""

    def _run(self, payload, env_extra=None):
        env = {**os.environ}
        env.pop("AGENTIC_ALLOW_GENERATED_EDIT", None)
        env.pop("AGENTIC_ALLOW_MAIN_EDIT", None)
        env["AGENTIC_ALLOW_MAIN_EDIT"] = "1"  # isolate from the main-tree guard
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )
        return proc.returncode, proc.stderr

    def test_edit_of_generated_file_is_blocked_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_manifest(root)
            target = root / "apps/web/src/generated/api/x.ts"
            payload = {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target)},
                "cwd": str(root),
            }
            code, err = self._run(payload)
            self.assertEqual(code, 2, err)
            self.assertIn(".claude/hooks/policy/generated", err)
            self.assertIn("AGENTIC_ALLOW_GENERATED_EDIT=1", err)

    def test_override_env_allows_the_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_manifest(root)
            target = root / "apps/web/src/generated/api/x.ts"
            payload = {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target)},
                "cwd": str(root),
            }
            code, _ = self._run(payload, {"AGENTIC_ALLOW_GENERATED_EDIT": "1"})
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
