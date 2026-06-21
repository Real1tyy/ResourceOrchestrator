# wt-cli

A clean, modular, tested implementation of the **spec-driven worktree lifecycle** —
the `wt:*` operations an agent runs in the AgenticWorkflow loop (create a worktree off
HEAD, rebase onto local `main`, self-review, recover a stale lock, land into `main`).

`scripts/wt.py` is the behavioural source of truth; this package conforms to it
(`spec-wt-cli-lifecycle-port`). Until the harness wiring is flipped, the `wt:*` Mise
tasks still call `scripts/wt.py` — see that spec's follow-ups.

## Install

```bash
uv tool install --editable /path/to/AgenticWorkflow/tools/wt-cli
```

After install, `wt --help` works from any directory. `wt` discovers the host repo via
`git rev-parse` from the current working directory.

## Commands

| Command | Description |
|---|---|
| `wt new <slug>` | Create an isolated worktree off HEAD under `.claude/worktrees/<slug>` (branch `worktree-<slug>`), then bootstrap it. |
| `wt rebase [slug]` | Rebase a worktree's branch onto **local `main`**; guides conflict resolution. In a worktree → acts on it; on main → slug or interactive pick. |
| `wt verify [slug]` | Self-review inputs: diff vs merge-base + untracked listing. |
| `wt warn-behind` | Non-fatal stale-base advisory (a gate preflight); always exits 0. |
| `wt unlock` | Remove a **stale**, age-guarded `index.lock` in the current worktree. The path is computed (never from an argument), so it can only ever touch *this* gitdir's lock. |
| `wt list` | List landable worktrees (slug, branch, ahead/behind vs `main`). |
| `wt land [slug]` | **Human-only**: fast-forward `main` to the branch + tear down. Auto-rebases onto `main` on divergence, then retries the ff; never mutates `main` until the ff succeeds. |

Run `wt <command> --help` for command-specific options.

## Lifecycle hooks (repo-owned, downstream-overridable)

The tool ships **no opinionated, repo-specific behaviour** (no node_modules symlinking,
no skills installation). Instead each lifecycle verb fires a generic pre/post hook that a
downstream repo can implement — the same template/extension pattern the harness
pre-commit hooks use.

- Default: a **no-op**. With no hook file present, nothing extra happens.
- Override file (repo-owned, **never overwritten** by any install/sync): default
  `<repo>/.config/wt/hooks.py`, or set `$WT_HOOKS_PATH`.
- Each verb fires `pre_<stage>` before and `post_<stage>` after its git work (e.g. `new`
  → `pre_new` / `post_new`), or a generic `pre(stage, ctx)` / `post(stage, ctx)` if the
  specific name is absent. `ctx` carries stage data (e.g. `slug`, `branch`, `worktree`,
  `main_root`).
- Contract: a missing file, missing function, or **any exception** inside a hook is a
  silent no-op (an extension only *adds* behaviour). A hook **blocks** its stage by
  exiting non-zero (`sys.exit(2)`).

Example — a downstream re-adds the old `node_modules` symlinking on `new`:

```python
# <repo>/.config/wt/hooks.py
def post_new(ctx):
    link_node_modules(ctx["main_root"], ctx["worktree"])
```

## Tests

```bash
mise run test:wt-cli        # pytest via uv (provides typer from the lockfile)
# or, directly:
cd tools/wt-cli && uv run --with pytest pytest -q
```
