"""`wt` — Typer CLI for the spec-driven worktree lifecycle.

A clean, modular, tested re-implementation of `scripts/wt.py` (the behavioural
source of truth — see spec-wt-cli-lifecycle-port). Subcommands mirror the
``wt:*`` Mise tasks the agent loop drives:

    new <slug>     create a worktree off HEAD under .claude/worktrees/<slug>, bootstrap it
    rebase [slug]  rebase the branch onto local main; guide conflict resolution
    verify [slug]  self-review inputs: diff vs merge-base
    warn-behind    non-fatal stale-base advisory (gate preflight)
    unlock         remove a STALE, age-gated index.lock in the current worktree
    list           list landable worktrees (slug, branch, ahead/behind)
    land [slug]    HUMAN-ONLY: fast-forward main to the branch + tear down

Repo-specific lifecycle behaviour (e.g. node_modules symlinking) is NOT baked in
— it goes in the repo-owned pre/post hook seam (see `hooks.py`).
"""

from __future__ import annotations

from typing import Annotated, Callable, Optional

import typer

from . import __version__
from .const import WtError

app = typer.Typer(
    name="wt",
    help="Spec-driven git worktree lifecycle — new/rebase/verify/unlock/list/land.",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wt {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show wt version and exit.",
        ),
    ] = None,
) -> None:
    """Root callback — wires `--version` and serves as the help anchor."""


def _dispatch(fn: Callable[[], None]) -> None:
    """Run a lifecycle function, mapping its `WtError` to a clean non-zero exit.

    `WtError` carries operator-facing guidance (often multi-line); we print it
    with the ``wt:`` prefix and exit 1, the structured analogue of
    `scripts/wt.py`'s ``die()``."""
    try:
        fn()
    except WtError as exc:
        typer.echo(f"wt: {exc}", err=True)
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


_Slug = Annotated[
    Optional[str],
    typer.Argument(help="Worktree slug. If omitted (on main), pick interactively."),
]


@app.command("new")
def new_cmd(
    slug: Annotated[
        Optional[str],
        typer.Argument(help="Slug (lowercase, digits, single hyphens) for the new worktree."),
    ] = None,
) -> None:
    """Create an isolated worktree off HEAD under .claude/worktrees/<slug>, then bootstrap it."""
    from .new_worktree import run_new

    _dispatch(lambda: run_new(slug))


@app.command("rebase")
def rebase_cmd(worktree: _Slug = None) -> None:
    """Rebase a worktree's branch onto local main; guide conflict resolution."""
    from .rebase_worktree import run_rebase

    _dispatch(lambda: run_rebase(worktree))


@app.command("verify")
def verify_cmd(worktree: _Slug = None) -> None:
    """Self-review inputs: diff vs merge-base (+ change report when the tool ships)."""
    from .verify_worktree import run_verify

    _dispatch(lambda: run_verify(worktree))


@app.command("warn-behind")
def warn_behind_cmd() -> None:
    """Print the stale-base advisory if the branch's base trails main (always exits 0)."""
    from .verify_worktree import run_warn_behind

    _dispatch(run_warn_behind)


@app.command("unlock")
def unlock_cmd() -> None:
    """Remove a STALE git index.lock in the CURRENT worktree (age-guarded; no path arg)."""
    from .lock import unlock

    _dispatch(lambda: unlock())


@app.command("list")
def list_cmd() -> None:
    """List landable worktrees (slug, branch, ahead/behind vs main)."""
    from .worktree import run_list

    _dispatch(run_list)


@app.command("land")
def land_cmd(worktree: _Slug = None) -> None:
    """HUMAN-ONLY: fast-forward main to the branch + tear down (auto-rebase on divergence)."""
    from .land_worktree import run_land

    _dispatch(lambda: run_land(worktree))


def main(argv: list[str] | None = None) -> int:
    """In-process entry — returns the exit code instead of raising SystemExit.

    Used by tests that need to invoke `wt` without a fork. Production `wt` calls
    `app()` directly via the `[project.scripts]` entry in pyproject.toml.
    """
    try:
        app(args=argv)
        return 0
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1


if __name__ == "__main__":
    app()
