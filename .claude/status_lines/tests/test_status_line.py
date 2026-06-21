#!/usr/bin/env python3
"""Unit tests for the bundle status line (assets/status_lines/agenticd.py).

Runs under bare `python3` (no deps) so it rides in `mise run test:hooks` next to the
hook tests. Imports the script by path and asserts on the ANSI-stripped output across
the payload shapes the status line must survive: effort present/absent, rate_limits
present / one-window / none, and null/early-session context fields.
"""

import importlib.util
import json
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "agenticd.py"
_spec = importlib.util.spec_from_file_location("agenticd_status_line", _SCRIPT)
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


# A fixed clock so reset-countdown text is deterministic.
NOW = 1_000_000


def render(payload):
    """generate_status_line with the frozen clock, ANSI stripped."""
    return sl.strip_ansi(sl.generate_status_line(payload, now=NOW))


class EffortSegment(unittest.TestCase):
    def test_effort_level_rendered(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            self.assertIn(f"eff:{level}", render({"effort": {"level": level}}))

    def test_effort_absent_is_omitted(self):
        self.assertNotIn("eff:", render({"model": {"display_name": "Opus"}}))

    def test_effort_empty_object_is_omitted(self):
        self.assertNotIn("eff:", render({"effort": {}}))

    def test_thinking_disabled_dims_but_still_shows_level(self):
        out = sl.generate_status_line(
            {"effort": {"level": "xhigh"}, "thinking": {"enabled": False}}, now=NOW
        )
        self.assertIn(sl.DIM, out)
        self.assertIn("eff:xhigh", sl.strip_ansi(out))


class RateLimitSegment(unittest.TestCase):
    def test_both_windows_with_reset_countdown(self):
        out = render(
            {
                "rate_limits": {
                    "five_hour": {"used_percentage": 31, "resets_at": NOW + 8040},
                    "seven_day": {"used_percentage": 58, "resets_at": NOW + 259200},
                }
            }
        )
        self.assertIn("5h 31% (2h14m)", out)
        self.assertIn("7d 58% (3d)", out)

    def test_rate_limits_absent_is_omitted(self):
        out = render({"model": {"display_name": "Opus"}})
        self.assertNotIn("5h ", out)
        self.assertNotIn("7d ", out)

    def test_single_window_present(self):
        out = render({"rate_limits": {"five_hour": {"used_percentage": 12}}})
        self.assertIn("5h 12%", out)
        self.assertNotIn("7d", out)

    def test_window_without_used_percentage_is_omitted(self):
        out = render({"rate_limits": {"five_hour": {"resets_at": NOW + 100}}})
        self.assertNotIn("5h", out)

    def test_past_reset_shows_no_countdown(self):
        out = render(
            {"rate_limits": {"seven_day": {"used_percentage": 90, "resets_at": NOW - 5}}}
        )
        self.assertIn("7d 90%", out)
        self.assertNotIn("(", out)


class ContextAndRobustness(unittest.TestCase):
    def test_preserves_model_context_and_session(self):
        out = render(
            {
                "model": {"display_name": "Opus 4.8"},
                "session_id": "abc123",
                "context_window": {"used_percentage": 43.0, "context_window_size": 1_000_000},
            }
        )
        self.assertIn("[Opus 4.8]", out)
        self.assertIn("43.0% used", out)
        self.assertIn("abc123", out)
        self.assertIn("left", out)

    def test_null_used_percentage_is_zero(self):
        out = render({"context_window": {"used_percentage": None}})
        self.assertIn("0.0% used", out)

    def test_null_current_usage_does_not_raise(self):
        out = render({"context_window": {"current_usage": None, "used_percentage": None}})
        self.assertIn("used", out)

    def test_empty_payload_does_not_raise(self):
        out = render({})
        self.assertIn("[Claude]", out)

    def test_missing_session_id_has_placeholder(self):
        self.assertIn("--------", render({}))


class Formatting(unittest.TestCase):
    def test_format_duration_units(self):
        self.assertEqual(sl.format_duration(0), "now")
        self.assertEqual(sl.format_duration(-10), "now")
        self.assertEqual(sl.format_duration(30), "<1m")
        self.assertEqual(sl.format_duration(45 * 60), "45m")
        self.assertEqual(sl.format_duration(2 * 3600 + 14 * 60), "2h14m")
        self.assertEqual(sl.format_duration(3 * 3600), "3h")
        self.assertEqual(sl.format_duration(3 * 86400), "3d")
        self.assertEqual(sl.format_duration(86400 + 2 * 3600), "1d2h")

    def test_format_tokens(self):
        self.assertEqual(sl.format_tokens(None), "0")
        self.assertEqual(sl.format_tokens(500), "500")
        self.assertEqual(sl.format_tokens(1500), "1.5k")
        self.assertEqual(sl.format_tokens(2_000_000), "2.00M")


class MainEntrypoint(unittest.TestCase):
    def test_invalid_json_exits_zero_with_fallback(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            old_stdin = sl.sys.stdin
            sl.sys.stdin = io.StringIO("not json {")
            try:
                with self.assertRaises(SystemExit) as cm:
                    sl.main()
            finally:
                sl.sys.stdin = old_stdin
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Error", buf.getvalue())

    def test_valid_json_renders_and_exits_zero(self):
        payload = json.dumps({"model": {"display_name": "Opus"}, "effort": {"level": "high"}})
        buf = io.StringIO()
        with redirect_stdout(buf):
            old_stdin = sl.sys.stdin
            sl.sys.stdin = io.StringIO(payload)
            try:
                with self.assertRaises(SystemExit) as cm:
                    sl.main()
            finally:
                sl.sys.stdin = old_stdin
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("eff:high", sl.strip_ansi(buf.getvalue()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
