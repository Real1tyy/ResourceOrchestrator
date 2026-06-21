#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

"""PostToolUse(Edit|Write|MultiEdit) → hand the one file Claude just edited to the
repo-owned formatter seam.

`agenticd` provides the *structure and opinion*; the downstream repo provides the
*formatting*. After every edit this hook resolves the edited file's path and calls
`format_file(<path>)` in the seed-once, repo-owned `.claude/hooks/format.py` (seeded
empty by `agenticd init`, then filled in per repo — see that file's docstring). Why
edit-time and single-file:

- **Edit-time, not commit-time.** Keeping each edited file formatted means the
  `pre-commit` format gate is a confirmation, not a discovery — it kills the
  commit→fmt-fail→re-stage→re-commit loop a real run paid for (efficiency finding
  2026-06-17).
- **Only the edited file.** The hook passes exactly one path, so a repo formatter that
  honours it never sweeps unrelated unstaged edits into the change (a whole-package
  `cargo fmt` would).

Best-effort and never-block: no seam, no `format_file`, a syntax error mid-edit, or any
formatter failure all exit 0 — a format hiccup must never wedge the edit. The bundle hook
stays generic: it knows how to *find* the edited file and *call* the seam, never which
formatter to run — that is the repo's call, so repo-specific tooling never rides the
bundle to every other repo ([[decision-edit-time-auto-format-hook]],
[[decision-hook-extension-points]])."""

import importlib.util
import json
import sys
from pathlib import Path

try:
    from _hook_error import fail_safe
except ImportError:
    def fail_safe(exc: BaseException, hook_name: str) -> None:  # noqa: ARG001
        sys.exit(0)

# The file-editing tools whose target this hook formats. NotebookEdit (notebook_path,
# not file_path) and every non-editing tool are intentionally excluded.
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})

# The repo-owned formatter seam, seeded empty by `agenticd init` beside this hook.
_SEAM_PATH = Path(__file__).resolve().parent / "format.py"


def extract_file_path(input_data: dict) -> str | None:
    """The edited file path for an Edit/Write/MultiEdit event, else ``None``. Pure."""
    if input_data.get("tool_name") not in _EDIT_TOOLS:
        return None
    fp = (input_data.get("tool_input") or {}).get("file_path")
    return fp if isinstance(fp, str) and fp else None


def format_via_seam(file_path: str) -> None:
    """Load the repo-owned ``format.py`` seam and run its ``format_file(file_path)``.

    Every failure mode — no seam file, no ``format_file`` symbol, an import error, or a
    formatter that raises — is swallowed: formatting is advisory and must never block the
    edit (mirrors the ``_extension`` seam contract)."""
    if not _SEAM_PATH.is_file():
        return
    try:
        spec = importlib.util.spec_from_file_location("agenticd_repo_format", _SEAM_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "format_file", None)
        if callable(fn):
            fn(file_path)
    except Exception:
        return


def main():
    try:
        input_data = json.load(sys.stdin)
        file_path = extract_file_path(input_data)
        if file_path and Path(file_path).is_file():
            format_via_seam(file_path)
        sys.exit(0)
    except json.JSONDecodeError as exc:
        fail_safe(exc, "post_tool_use_format")
    except Exception as exc:
        fail_safe(exc, "post_tool_use_format")


if __name__ == "__main__":
    main()
