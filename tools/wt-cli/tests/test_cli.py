#!/usr/bin/env python3
"""Tests for the `wt` Typer entrypoint — the worktree-lifecycle command surface.

`wt` is built on Typer, so the exit-code and stderr conventions follow Click's:
  - `--help` / `-h`         → exit 0, help on stdout
  - no command              → exit 2 (missing command)
  - unknown command         → exit 2, "No such command" on stderr
  - `--version`             → exit 0, version on stdout

These pin the contract so a refactor doesn't silently drift.
"""

from __future__ import annotations

import subprocess
import unittest

from tests._helpers import PY, cli_env
from wt_cli import __version__
from wt_cli.cli import main as cli_main

_VERBS = ("new", "rebase", "verify", "warn-behind", "unlock", "list", "land")


def _run(*args, check=False):
    return subprocess.run(
        [PY, "-m", "wt_cli.cli", *args],
        capture_output=True,
        text=True,
        env=cli_env(),
        check=check,
    )


class DispatcherTest(unittest.TestCase):
    def test_no_args_prints_usage_with_exit_2(self):
        result = _run()
        self.assertEqual(result.returncode, 2)
        haystack = result.stdout + result.stderr
        for verb in ("new", "rebase", "land"):
            self.assertIn(verb, haystack)

    def test_help_flag_prints_all_verbs_with_exit_0(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                result = _run(flag)
                self.assertEqual(result.returncode, 0)
                for verb in _VERBS:
                    self.assertIn(verb, result.stdout)

    def test_help_does_not_mention_removed_commands(self):
        result = _run("--help")
        # Distinctive names of the removed Obsidian/skills surface. ("merge"/"delete"
        # are skipped — they appear as substrings of legit help text like "merge-base".)
        for gone in ("apply", "checkpoint", "install-skills", "link-deps"):
            self.assertNotIn(gone, result.stdout)

    def test_version_flag(self):
        result = _run("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn(__version__, result.stdout)

    def test_unknown_command_exits_2(self):
        result = _run("definitely-not-a-command")
        self.assertEqual(result.returncode, 2)
        haystack = (result.stdout + result.stderr).lower()
        self.assertIn("no such command", haystack)

    def test_subcommand_help_pass_through(self):
        result = _run("new", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("HEAD", result.stdout)

    # In-process — fast, no subprocess overhead.

    def test_in_process_help(self):
        self.assertEqual(cli_main(["--help"]), 0)

    def test_in_process_no_args(self):
        self.assertEqual(cli_main([]), 2)

    def test_in_process_unknown(self):
        self.assertEqual(cli_main(["bogus"]), 2)

    def test_in_process_version(self):
        self.assertEqual(cli_main(["--version"]), 0)


if __name__ == "__main__":
    unittest.main()
