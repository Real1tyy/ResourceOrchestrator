#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

"""Unit tests for the generic PostToolUse auto-format dispatcher (efficiency finding
2026-06-17).

This is the BUNDLE hook: it only resolves the edited file and delegates to the
repo-owned `format.py` seam. A repo's actual formatter (e.g. rustfmt/prettier routing) is
its own concern and is tested by that repo (here, `test_format.py`). So these tests
assert the *generic contract*: which tool events yield a path to format, and that
delegation to the seam is best-effort — every failure mode is swallowed, never raised."""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import post_tool_use_format as fmt  # noqa: E402


class TestExtractFilePath(unittest.TestCase):
    def _ev(self, tool, ti):
        return {"tool_name": tool, "tool_input": ti}

    def test_edit_write_multiedit(self):
        for tool in ("Edit", "Write", "MultiEdit"):
            self.assertEqual(
                fmt.extract_file_path(self._ev(tool, {"file_path": "/x/y.rs"})), "/x/y.rs"
            )

    def test_non_edit_tool_ignored(self):
        self.assertIsNone(fmt.extract_file_path(self._ev("Bash", {"command": "ls"})))
        self.assertIsNone(fmt.extract_file_path(self._ev("Read", {"file_path": "/x/y.rs"})))
        # NotebookEdit carries notebook_path, not file_path — deliberately excluded.
        self.assertIsNone(
            fmt.extract_file_path(self._ev("NotebookEdit", {"notebook_path": "/x/y.ipynb"}))
        )

    def test_missing_or_blank_path(self):
        self.assertIsNone(fmt.extract_file_path(self._ev("Edit", {})))
        self.assertIsNone(fmt.extract_file_path(self._ev("Edit", {"file_path": ""})))


class TestSeamDelegation(unittest.TestCase):
    """`format_via_seam` loads `format.py` beside the hook and runs its `format_file`.
    A throwaway seam keeps the test independent of the repo's real formatter."""

    def setUp(self):
        self._orig_seam = fmt._SEAM_PATH
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(fmt, "_SEAM_PATH", self._orig_seam))
        self.dir = Path(self._tmp.name)

    def _write_seam(self, body: str) -> None:
        seam = self.dir / "format.py"
        seam.write_text(textwrap.dedent(body))
        fmt._SEAM_PATH = seam

    def test_calls_format_file_with_the_edited_path(self):
        marker = self.dir / "got.txt"
        self._write_seam(f"""
            from pathlib import Path

            def format_file(file_path):
                Path({str(marker)!r}).write_text(file_path)
        """)
        fmt.format_via_seam("/x/y.rs")
        self.assertEqual(marker.read_text(), "/x/y.rs")

    def test_absent_seam_is_noop(self):
        fmt._SEAM_PATH = self.dir / "nope.py"
        fmt.format_via_seam("/x/y.rs")  # must not raise

    def test_seam_without_format_file_is_noop(self):
        self._write_seam("X = 1\n")
        fmt.format_via_seam("/x/y.rs")  # must not raise

    def test_seam_that_raises_is_swallowed(self):
        self._write_seam("""
            def format_file(file_path):
                raise RuntimeError("boom")
        """)
        fmt.format_via_seam("/x/y.rs")  # must not raise


if __name__ == "__main__":
    unittest.main()
