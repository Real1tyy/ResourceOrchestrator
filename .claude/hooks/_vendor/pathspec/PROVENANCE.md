# Vendored: `pathspec`

| | |
|---|---|
| **Package** | `pathspec` |
| **Version** | `0.12.1` (pinned) |
| **License** | MPL 2.0 (see `LICENSE`) |
| **Source** | https://pypi.org/project/pathspec/0.12.1/ |
| **Upstream** | https://github.com/cpburnz/python-pathspec |
| **Vendored** | 2026-06-17 |

## Why this is here

`pre_tool_use.py` needs `pathspec`'s gitwildmatch matcher for the generated-files
edit guard (`_generated_file_edit_blocked`). Declaring it as a PEP 723 inline
dependency made `uv run --script` resolve a dependency environment on **every**
PreToolUse invocation — a measured ~44–50 ms per-call tax even warm-cached, and
the source of ~1 s cold-cache spikes (spec-hook-dedep-pathspec). `pathspec` is
pure-Python, so committing it here and putting `_vendor/` on `sys.path` lets the
hook declare `dependencies = []` and run at interpreter speed — no resolution, no
network, no install step.

## Upgrading

This is a deliberate, reviewable diff — not an auto-update:

1. `uv pip download pathspec==<new> --no-deps --no-binary :all:` (or grab the
   wheel) and extract the pure-Python `pathspec/` package.
2. Replace this directory's `*.py` + `py.typed` with the new source.
3. Update the **Version** + **Vendored** rows above and the version pin referenced
   in `pre_tool_use.py`'s `_vendor` comment.
4. Re-run the §12.6 hook rebuild loop and `mise run test:hooks`.

Only the pure-Python package is vendored (no `dist-info`, no compiled artifacts).
