<!-- agenticd:managed-doc — DO NOT hand-edit this file. It is a generated mirror,
     byte-synced by `agenticd init` from the bundle template at
     crates/agenticd-claude/assets/docs/AGENTS.md. To change it: edit that bundle
     template, then run `agenticd init` to sync. -->

# AGENTS.md — How agents work in this repo

> Seeded by `agenticd init`. This is the first thing an agent should read.
> It tells you the model this repo runs on, what you are allowed to do, and
> how a unit of work travels from spec to merged.

---

## The model: one spec, one worktree, one agent

Every non-trivial task in this repo is driven by the **spec-driven worktree loop**:

```
planner writes a spec in docs/specs/
  → agent picks up the spec (/execute-local-worktree)
    → agent creates a dedicated worktree (mise run wt:new <slug>)
      → agent implements, commits incrementally, rebases
        → agent marks spec done, writes retrospective
          → session stops → Stop hook runs pre-stop
            → human reviews and lands the branch
```

One spec per worktree. No bundling. No starting without a spec. The loop closes
when the spec is `accepted`, the retrospective is committed, and the branch is
clean and rebased.

The skill that drives this loop is `/execute-local-worktree` (`.claude/skills/`).
Read it before starting work — it is the authoritative step-by-step guide.

---

## The rebase model

This repo uses the **rebase model** for integrating worktree branches into `main`.

### How it works

1. **Agent commits incrementally.** Inside the worktree, one coherent chunk at a
   time (a feature plus its tests), with Conventional Commit messages.
   `pre-commit` (`mise run pre-commit`) must pass on every commit before
   moving to the next chunk.

2. **Agent rebases the branch onto `main`** (inside the worktree):
   ```bash
   mise run wt:rebase   # rebases onto LOCAL main (not origin — origin is stale)
   ```
   Conflicts are resolved here, in the worktree, keeping both sides' intent.
   This makes the human's eventual fast-forward clean and conflict-free.

3. **Human lands the branch** (`git merge --ff-only <branch>`). Because the
   branch was already rebased, this is a pure fast-forward — no merge commit,
   no conflicts on `main`. This is the human's op; the agent stops before it.

### Why not merge commits?

Merge commits clutter history and allow conflicts to land directly on `main`.
The rebase model keeps `main` linear and pushes all conflict resolution into
the isolated worktree, before any human review.

### Why not one large uncommitted diff?

Incremental commits give per-chunk `pre-commit` feedback and a reviewable history.
A single diff handed to the human forces them to do the commit-slicing chore.

### The ordering is deliberate

"Rebase the branch onto `main`, then fast-forward `main` to the branch" — not
the reverse. You replay the agent's commits on top of the latest `main`, then
advance `main` to the tip. Never rebase `main` itself.

---

## What agents are allowed to do

Agent capability is **default-deny**. A `PreToolUse` hook (`.claude/hooks/`)
blocks any Bash command not explicitly allowed and names the sanctioned
alternative. This keeps agents focused, not boxed in.

### What's allowed

- **Lifecycle operations through `wt:*` tasks** — the only way to interact
  with the worktree lifecycle:
  - `mise run wt:new <slug>` — create a worktree (bootstraps via `wt:setup`)
  - `mise run wt:rebase` — rebase the branch onto local `main`
  - `mise run wt:verify` — self-review: diff vs merge-base
  - `mise run wt:unlock` — remove a stale `index.lock`
  - `mise run pre-commit` — the lightweight per-commit checks (run automatically on every commit, but can run manually)
  - `mise run pre-stop` — the heavy hand-off checks (run automatically on Stop, but can run manually)
  - `mise run change-report` — generate a diff report for the worktree
- **Incremental git inside your worktree** — `git add`, `git commit`,
  `git status`/`log`/`diff`, `git rebase --continue`/`--abort`, `git stash`.
  These pass because you are inside a worktree under `.claude/worktrees/`.
- **File edits inside your worktree** — Write/Edit to any path under
  `.claude/worktrees/<your-worktree>/`.

### What's blocked

- **Commits from the main checkout** — use a worktree (enter it first).
- **File edits in the main checkout** — use a worktree.
- `git push --force` — use `--force-with-lease`.
- `git reset --hard` — diagnose before destroying.
- `--no-verify` — fix the failing check, never skip it.
- `mise run wt:land` — that is the human's Stage-8 op.
- The full e2e suite / pushing — human-only; mines the CPU.
- Anything not on the allowlist (`.claude/hooks/policy/allowlist.toml`).

Human escape hatches (never for agents):
- `AGENTIC_ALLOW_MAIN_COMMIT=1` — allow a commit from main.
- `AGENTIC_ALLOW_MAIN_EDIT=1` — allow a file edit in main.
- `AGENTIC_ALLOW_UNVERIFIED_HANDOFF=1` — bypass the Stop-hook check.

---

## The hand-off checks (`pre-stop`)

When a Claude Code session stops, the **Stop hook** fires automatically:

1. **Rebase check** — blocks if the branch is behind `main`. Fix: `mise run wt:rebase`.
2. **`mise run pre-stop`** — runs the heavy hand-off checks: change-report, then all
   `check` steps, then the changed-spec e2e. Blocks if it exits non-zero.

You do not have to remember to run this yourself — it fires on every stop. If it
blocks you, fix the issue and stop again. Run `mise run pre-stop` manually to
iterate without waiting for the stop. There is **no `PreStop` hook event** —
`pre-stop` is the *task*; the *invocation* is the `Stop` hook (it runs just before
the stop is allowed to complete).

`pre-stop` orchestrates the repo-owned `check:*` / `test:*` bodies (seeded as no-op
stubs in a fresh repo; downstream repos fill in their stack's real commands).

---

## The task harness

All operations go through `mise run <task>`. The tasks are defined in
`.config/mise.toml`. The full reference:

| Task | What it does |
|---|---|
| `wt:new <slug>` | Create a worktree; auto-runs `wt:setup` |
| `wt:setup` | Bootstrap a fresh worktree: install deps, format tree |
| `wt:rebase` | Rebase branch onto local `main` |
| `wt:verify` | Self-review: diff vs merge-base |
| `wt:unlock` | Remove a stale `index.lock` |
| `wt:land` | Fast-forward `main` + teardown — **HUMAN ONLY** |
| `wt:list` | List active worktrees and status |
| `pre-commit` | Stack-specific per-commit checks (seeded no-op; downstream fills in) |
| `pre-stop` | Heavy hand-off checks (change-report + checks + changed-spec e2e); runs on Stop hook + manually |
| `change-report` | Generate a diff/change report for the worktree |

---

## Docs in this repo

| File | What it covers |
|---|---|
| `CLAUDE.md` | Binding conventions — **read first, follow always** |
| `docs/AGENTS.md` | This file — the agent workflow model |
| `docs/DOCS.md` | Documentation conventions (specs, ADRs, memory, knowledge) |
| `docs/TOOLS.md` | Toolchain, checks, and CI — mise, lefthook, testing |
| `docs/specs/` | Work specs — one per worktree, one per agent run |
| `docs/decisions/` | ADRs — hard-to-reverse architecture decisions |
| `docs/memory/` | Sprint retrospectives — what agents learned |
| `docs/knowledge/` | Reference notes and vendor-doc snapshots |

---

## Commit messages

This repo enforces [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): description`. The `commit-msg` lefthook rejects non-conforming
messages. Valid types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.

**Never** append agent-attribution trailers (`Co-Authored-By: Claude …`,
`🤖 Generated with …`). The commit body describes the change only.
