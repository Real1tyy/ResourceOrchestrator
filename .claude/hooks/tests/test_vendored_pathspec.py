#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Tests that ``pathspec`` is resolved from the VENDORED copy, not a uv/pip-installed
one (spec-hook-dedep-pathspec).

The point of vendoring is that ``pre_tool_use.py`` declares ``dependencies = []`` and
still gets its gitwildmatch matcher — so ``uv run --script`` runs at interpreter speed
instead of paying the ~44–50 ms per-call dependency-environment tax. These tests pin
that contract:

- ``pathspec`` imports under ``python3 -I -S`` (isolated + no site-packages → the only
  way it can resolve is from the vendored dir we put on ``sys.path``: no uv, no network,
  no third-party install).
- The resolved module physically lives under ``_vendor/`` and is the pinned 0.12.1.
- Its gitwildmatch behaviour is intact (the exact API the guard calls).
- Importing ``pre_tool_use`` wires the matcher up (``pathspec is not None``) from the
  vendored location.

Runs under a bare ``python3`` via ``mise run test:hooks``.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
VENDOR_DIR = HOOKS_DIR / "_vendor"
sys.path.insert(0, str(HOOKS_DIR / "validators"))
sys.path.insert(0, str(HOOKS_DIR))


class TestVendoredPathspecResolution(unittest.TestCase):
    def test_imports_isolated_from_vendor_only(self):
        """Under ``python3 -I -S`` (no site-packages, no env paths) the ONLY way
        ``pathspec`` resolves is from the vendored dir — proving zero third-party
        resolution (no uv, no network)."""
        probe = (
            "import sys, json;"
            f"sys.path.insert(0, {str(VENDOR_DIR)!r});"
            "import pathspec;"
            "spec = pathspec.PathSpec.from_lines('gitwildmatch', ['apps/web/src/generated/']);"
            "print(json.dumps({"
            "'file': pathspec.__file__,"
            "'version': pathspec.__version__,"
            "'match_hit': spec.match_file('apps/web/src/generated/api/x.ts'),"
            "'match_miss': spec.match_file('apps/web/src/app.tsx'),"
            "}))"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", probe],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(
            Path(data["file"]).resolve().is_relative_to(VENDOR_DIR.resolve()),
            f"pathspec must resolve from {VENDOR_DIR}, got {data['file']}",
        )
        self.assertEqual(data["version"], "0.12.1")
        self.assertTrue(data["match_hit"])
        self.assertFalse(data["match_miss"])

    def test_pre_tool_use_wires_the_vendored_matcher(self):
        """Importing the hook makes its ``pathspec`` attribute resolve (not the
        fail-open ``None``) from the vendored copy — the exact wiring the guard uses."""
        import pre_tool_use  # noqa: PLC0415 (import-in-test by design)

        self.assertIsNotNone(
            pre_tool_use.pathspec,
            "pre_tool_use.pathspec must resolve from the vendored copy, not be None",
        )
        self.assertTrue(
            Path(pre_tool_use.pathspec.__file__).resolve().is_relative_to(VENDOR_DIR.resolve()),
            f"the hook's pathspec must come from {VENDOR_DIR}",
        )
        self.assertEqual(pre_tool_use.pathspec.__version__, "0.12.1")


if __name__ == "__main__":
    unittest.main()
