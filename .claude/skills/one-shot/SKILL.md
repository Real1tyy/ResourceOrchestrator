---
name: one-shot
disable-model-invocation: true
description: One-shot mode — you are (or will be) in an isolated worktree; implement a small, well-understood change as fast as possible and hand off. A deliberate deviation from the full spec→plan→execute→retrospective loop — NO spec, NO planning, NO acceptance-criteria ticking, NO retrospective ceremony, NO questions. The quality bar still holds: commit incrementally, rebase onto main, green `mise run pre-commit`, hand off. Never land. Use when the user says "just do X", "quick change", "one-shot this", or invokes /one-shot.
---

# One-shot — Implement Now, No Paperwork

This is the **fast path** beside the Planner/Execute loop. Not every change deserves
spec → plan → execute → retrospective. Small, well-understood changes ("add this,
tweak that") want to be **built immediately**. That is this skill's only job: you are
in a worktree — implement the assignment as fast as possible, keep `pre-commit` green,
hand off.

**Sweet spot:** small, well-understood changes the maintainer could describe in a
sentence. If the work turns out to genuinely need a spec (cross-cutting, a schema/
persistence decision, an ADR), say so in the hand-off and stop — the Planner is one
button over. Don't quietly grow a one-shot into a half-planned epic.

## The one deliberate deviation

This skill **drops the planning ceremony, not the quality bar**:

- **NO** spec — one-shot produces no `docs/specs/` file.
- **NO** planning conversation, **NO** AskUserQuestion quizzes, **NO** questions to
  the user at all. You were given the assignment; build it.
- **NO** ticking acceptance criteria (there's no spec to tick).
- **NO** mandatory retrospective. Write `docs/memory/<…>.md` **only** if the task was
  genuinely hard or surprised you; otherwise skip it (no empty "nothing surprising"
  notes).

What is **NOT** dropped: incremental commits, Conventional Commits, the rebase onto
`main`, and a **green `mise run pre-commit`** before hand-off. "Fast" means skipping
paperwork — never correctness. The repo never ships red.

## Capability posture: default-deny (spec 0031)

Your Bash/file capability is **default-deny** — a `PreToolUse` hook (allowlist at
`.claude/hooks/policy/allowlist.toml`) blocks any command not explicitly allowed and
**blocks `Write`/`Edit` whose target is in the main checkout** (outside
`.claude/worktrees/`). All implementation happens inside your worktree. If a write is
blocked, you are not inside the worktree — enter one first (see Step 1).

- **Lifecycle ops go through `wt:*` tasks** — `mise run wt:new`,
  `wt:rebase`. Never raw `git worktree add`. `mise run wt:land` is the human's op —
  off your allowlist; **never land.**
- **Incremental git inside the worktree is allowed** — `git add/commit/status/log/
  diff`, `git rebase --continue`, etc. Running git yourself is the rebase model.

## The loop

### Step 1 — Ensure you are in a worktree

Check your branch and cwd:

```bash
git rev-parse --abbrev-ref HEAD
pwd
```

- **Already inside `.claude/worktrees/<…>` on a non-`main` branch** (the daemon's
  One-shot button always lands you here) → **implement in place.** Do NOT create a
  second worktree — that nests one.
- **On `main`/`master`** → create and enter a worktree first:
  `mise run wt:new <slug>` (a short kebab slug from the task, e.g.
  `add-export-button`), then use the `EnterWorktree` tool / work inside it.

When in doubt (detached HEAD, unusual cwd), prefer the create path.

### Step 2 — Implement, as fast as possible

- Re-read **only** what you need: `CLAUDE.md` stack rules (the binding conventions),
  and the code you're about to touch. Don't spelunk the whole repo.
- Build the change directly. Follow existing patterns in the files you edit.
- **Do not ask the user questions.** If a detail is genuinely ambiguous, pick the
  most reasonable interpretation, implement it, and note the assumption in the
  hand-off. The maintainer reviews the diff.

### Step 3 — Commit incrementally

One coherent commit per chunk (feature + its tests), Conventional Commit messages.
`pre-commit` runs on each commit — keep each chunk green before the next.

```bash
git add <files for this chunk>
git commit -m "feat(scope): the change"
```

**Never** `--no-verify`. **Never** append attribution trailers (no
`Co-Authored-By`, no "Generated with Claude Code") — see CLAUDE.md.

Write tests for what you change: a new behavior gets a test; a bug fix gets a
reproducing test. `pre-commit` enforces it and review depends on it.

### Step 4 — Rebase onto `main`

With chunks committed and the tree clean (`git status --short`):

```bash
mise run wt:rebase     # rebases onto LOCAL main; guides conflicts
```

Resolve any conflicts keeping both sides' intent, then re-green `pre-commit`. Rebase
**once** — don't chase a moving `main`; the watch-main daemon keeps an idle, clean
branch current, and the human re-rebases at land time. Never poll or race an
`index.lock`.

### Step 5 — Green `pre-commit`

```bash
mise run pre-commit    # lint, test, build, type-check
```

This is **non-optional.** Hand-off requires a green `pre-commit`. Fix the root cause of
any failure; never suppress it. If `pre-commit` fails on a **pre-existing** issue your diff
doesn't touch, call it out in the hand-off and leave it for a separate fix.

### Step 6 — Hand off (never land)

Stop and summarize. Do **not** fast-forward `main`, push to `main`, or tear down —
landing is the human's op ([[decision-worktree-rebase-model]]).

```
One-shot Complete — ready for human review

Worktree: <worktree-name>
Branch:   <branch-name>  (rebased onto main @ <short-sha>)
Commits:  <n> incremental commits
Files:    <count> changed, <count> added
Pre-commit: ✅  | rebase: clean onto main

What landed: <one-paragraph summary of the change>
Assumptions: <any ambiguity you resolved, or "none">

Next (your op): review the diff, then fast-forward main to this branch and tear down:
  git switch main
  git merge --ff-only <branch-name>
  git worktree remove .claude/worktrees/<worktree-name>
  git branch -d <branch-name>
```

### Step 7 — Retrospective (only if it was hard)

If the run hit genuine difficulty — a non-obvious bug, a missing pattern, tooling
friction worth fixing — create it with `mise run docs:new memory --json '{"name": "<slug>",
"body": "<findings>"}'` (a direct `Write` of a new doc is blocked by the hook → use the
tool, then `Edit` to refine) per `docs/DOCS.md §6`, one actionable finding per section,
labelled `[Code]` or `[Process]`. **Skip it** for a routine run. A one-shot that went smoothly leaves no retrospective — that's
correct, not a gap.

## Critical rules

- **NEVER write a spec, tick acceptance criteria, or run the planning/retrospective
  loop.** That's the whole point — one-shot is the no-paperwork path.
- **NEVER ask the user questions.** Resolve ambiguity yourself and note it.
- **NEVER skip `pre-commit`.** Green `mise run pre-commit` before hand-off; no `--no-verify`.
- **NEVER land.** No fast-forward of `main`, no push to `main`, no teardown — the
  human lands.
- **ALWAYS work inside the worktree.** Never touch the main checkout.
- **ALWAYS commit incrementally** with Conventional Commits, no attribution trailers.
