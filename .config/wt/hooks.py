"""Repo-local worktree-lifecycle hooks for the `wt` CLI (tools/wt-cli).

SEED-ONCE + REPO-OWNED: `agenticd init` writes this stub only when it is absent and
never overwrites it (not even under `--force`), so your edits always survive. It
ships doing NOTHING — every hook below is a no-op. Fill them in to add behaviour at
a lifecycle stage without forking the bundled tool.

The `wt` CLI fires, for each lifecycle verb (`new`/`rebase`/`verify`/`land`), a
`pre_<stage>` before the git work and a `post_<stage>` after it — or the generic
`pre(stage, ctx)` / `post(stage, ctx)` below if the specific function is absent.

`ctx` is a dict with stage data, e.g. for `new`:
    ctx["slug"]       -> "my-feature"
    ctx["branch"]     -> "worktree-my-feature"
    ctx["worktree"]   -> Path to the new worktree
    ctx["main_root"]  -> Path to the main checkout

Contract (same as the harness pre-commit extension seam):
  * A missing function or ANY exception here is a silent no-op — a hook only ever
    ADDS behaviour, it never breaks the lifecycle op (the committed work is safe).
  * To BLOCK a stage, exit non-zero: `import sys; sys.exit(2)`.

Override location is `<repo>/.config/wt/hooks.py` by default ($WT_HOOKS_PATH wins).

Example — re-add node_modules symlinking on worktree creation (opt-in per repo):

    # def post_new(ctx):
    #     from my_repo_tooling import link_node_modules
    #     link_node_modules(ctx["main_root"], ctx["worktree"])
"""

from __future__ import annotations


def pre(stage: str, ctx: dict) -> None:
    """Runs before every lifecycle stage. No-op by default."""


def post(stage: str, ctx: dict) -> None:
    """Runs after every lifecycle stage. No-op by default."""
