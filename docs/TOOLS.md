# TOOLS.md — Toolchain, Checks, and CI

> Seeded by `agenticd init`. Update to document your repo's actual toolchain.

## 1. Task harness

All operations go through **Mise** (`.config/mise.toml`). Key tasks:

| Task | What it does |
|------|-------------|
| `mise run pre-commit` | Fast per-commit checks (lint, format, typecheck, unit tests) |
| `mise run pre-stop` | Full pipeline: every check step + changed-spec e2e |
| `mise run wt:new <slug>` | Create a worktree off HEAD |
| `mise run wt:rebase` | Rebase the current worktree branch onto local `main` |
| `mise run wt:land <slug>` | Fast-forward `main` to the branch and tear down (human-only) |
| `mise run wt:unlock` | Clear a stale `index.lock` |
| `mise run setup` | Bootstrap all runtime deps after a fresh worktree |
| `mise run format` | Auto-format the entire source tree |
| `mise run install-hooks` | Wire lefthook into `.git/hooks/` |

## 2. The `pre-commit` checks

`pre-commit` runs on every commit via lefthook + `mise run pre-commit`. It includes:

- Format check
- Lint
- Typecheck (if applicable)
- Unit tests

The full pipeline (`mise run pre-stop`) additionally runs e2e specs for the files changed in the current branch.

## 3. Lefthook

Git-level guards wired by `agenticd init`:

| Hook | Command | What it blocks |
|------|---------|---------------|
| `pre-commit: commit-location` | `mise run guard:commit` | Commits from the main checkout |
| `pre-commit: doc-names` | `mise run guard:doc-names` | Non-timestamp doc filenames |
| `pre-commit: stale-staged` | `mise run guard:stale-staged` | Stale staged files (warn only) |
| `commit-msg: conventional` | `cog verify …` | Non-Conventional-Commit messages |

## 4. Definition of Done

A spec is done when:
1. All `- [ ]` acceptance criteria are ticked.
2. `mise run pre-stop` is green on the final committed state.
3. The spec `status` is set to `accepted`.
4. A retrospective exists in `docs/memory/` (if anything was surprising).
5. The worktree is handed off (not landed — that is the human's op).
