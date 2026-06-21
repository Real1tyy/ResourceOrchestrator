"""Test bootstrap + the shared `main_repo` fixture.

Adding `src/` to sys.path lets `from wt_cli.repo import …` resolve without an
editable install. Subprocess invocations that exec `python -m wt_cli.cli` get the
same treatment via PYTHONPATH — see `_helpers.cli_env`. Git-repo construction
helpers live in `tests/_repo.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tests._repo import init_repo  # noqa: E402  (after sys.path setup)


@pytest.fixture
def main_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo on `main`, with cwd chdir'd into it (so `main_root()` /
    `in_worktree()`, which read the process cwd, resolve against it)."""
    repo = tmp_path / "repo"
    init_repo(repo)
    monkeypatch.chdir(repo)
    return repo
