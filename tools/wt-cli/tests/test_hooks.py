"""The generalized pre/post lifecycle hook seam (replaces opinionated link-deps).

Contract: no file ⇒ no-op; `pre_/post_<stage>` fire with ctx; a generic
`pre/post(stage, ctx)` is the fallback; any exception inside a hook is swallowed;
a non-zero exit BLOCKS the stage; `$WT_HOOKS_PATH` overrides the location.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wt_cli.const import WtError
from wt_cli.hooks import DEFAULT_HOOKS_REL, hook_path, run_hook


def _write_default_hook(repo: Path, body: str) -> Path:
    path = repo / DEFAULT_HOOKS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_no_file_is_noop(tmp_path: Path):
    # No override file → nothing happens, no exception.
    run_hook("new", "post", repo=tmp_path, ctx={})


def test_specific_hook_fires_with_ctx(tmp_path: Path):
    marker = tmp_path / "fired"
    _write_default_hook(
        tmp_path,
        "def post_new(ctx):\n"
        f"    open(r'{marker}', 'w').write(ctx['slug'])\n",
    )
    run_hook("new", "post", repo=tmp_path, ctx={"slug": "demo"})
    assert marker.read_text() == "demo"


def test_generic_hook_receives_stage(tmp_path: Path):
    marker = tmp_path / "fired"
    _write_default_hook(
        tmp_path,
        "def post(stage, ctx):\n"
        f"    open(r'{marker}', 'w').write(stage)\n",
    )
    run_hook("land", "post", repo=tmp_path, ctx={})
    assert marker.read_text() == "land"


def test_specific_wins_over_generic(tmp_path: Path):
    marker = tmp_path / "fired"
    _write_default_hook(
        tmp_path,
        "def post_new(ctx):\n"
        f"    open(r'{marker}', 'w').write('specific')\n"
        "def post(stage, ctx):\n"
        f"    open(r'{marker}', 'w').write('generic')\n",
    )
    run_hook("new", "post", repo=tmp_path, ctx={})
    assert marker.read_text() == "specific"


def test_exception_inside_hook_is_swallowed(tmp_path: Path):
    _write_default_hook(tmp_path, "def post_new(ctx):\n    raise RuntimeError('boom')\n")
    # Must NOT raise — an extension only ever adds behaviour.
    run_hook("new", "post", repo=tmp_path, ctx={})


def test_nonzero_exit_blocks_stage(tmp_path: Path):
    _write_default_hook(tmp_path, "import sys\ndef pre_new(ctx):\n    sys.exit(2)\n")
    with pytest.raises(WtError):
        run_hook("new", "pre", repo=tmp_path, ctx={})


def test_zero_exit_does_not_block(tmp_path: Path):
    _write_default_hook(tmp_path, "import sys\ndef pre_new(ctx):\n    sys.exit(0)\n")
    # Explicit exit(0) is a no-op success, not a block.
    run_hook("new", "pre", repo=tmp_path, ctx={})


def test_missing_stage_function_is_noop(tmp_path: Path):
    _write_default_hook(tmp_path, "def post_land(ctx):\n    raise RuntimeError('should not fire')\n")
    # We ask for post_new; only post_land is defined → no-op.
    run_hook("new", "post", repo=tmp_path, ctx={})


def test_env_override_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    custom = tmp_path / "elsewhere" / "myhooks.py"
    custom.parent.mkdir(parents=True)
    marker = tmp_path / "fired"
    custom.write_text(f"def post_new(ctx):\n    open(r'{marker}', 'w').write('x')\n")
    monkeypatch.setenv("WT_HOOKS_PATH", str(custom))
    assert hook_path(tmp_path) == custom
    run_hook("new", "post", repo=tmp_path, ctx={})
    assert marker.exists()
