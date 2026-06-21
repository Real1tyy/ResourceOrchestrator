---
name: merge-worktree
disable-model-invocation: true
description: Rebase a worktree branch onto the latest main from inside the worktree, resolve any conflicts locally, then iterate the project's pre-commit until clean. Use when the user says "merge worktree", "rebase onto main", "sync worktree with main", "resolve worktree conflicts", or invokes /merge-worktree.
---

# Merge Worktree (rebase onto `main`)

Integration step that runs **inside** a worktree. This repo uses the **rebase model** (ADR: worktree-rebase-model): the agent rebases its worktree branch onto the latest `main`, resolves conflicts locally, and re-greens `pre-commit` — so the human's eventual fast-forward of `main` to this branch is clean, with nothing to negotiate.

This is **Stage 3** of the worktree lifecycle (see `execute-local-worktree`). It maps to the first of the two integration operations:

1. **Rebase the worktree branch *onto* latest `main`** — this skill.
2. **Fast-forward `main` *to* the branch** — Stage 8, the human's op.

There is no "rebasing from the worktree to `main`." You **rebase the branch onto `main`** with plain git and resolve conflicts by hand.

**Your job:** rebase the branch onto `main`, resolve any conflict markers cleanly keeping both sides' intent, then iterate `mise run pre-commit` until it exits 0.

> **Capability posture (spec 0031).** Your Bash surface is default-deny. The sanctioned one-shot for the rebase is **`mise run wt:rebase`** (it rebases the branch onto local `main`). Resolving conflicts then continuing with raw git — `git status`, `git add <file>`, `git rebase --continue`, `git rebase --abort` — is **allowed because you are inside a worktree** under `.claude/worktrees/`. What stays blocked: `git reset --hard`, `git push --force` (use `--force-with-lease`), `--no-verify`, and rewriting `main`. The per-commit checks run as `mise run pre-commit`.

## Input

No arguments. You're already inside the worktree (your CWD should be `.claude/worktrees/<name>` or similar). If the user invokes you from elsewhere, `cd` into the worktree first.

## Instructions

### Step 1: Confirm you are in a worktree

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse --git-common-dir
git rev-parse --git-dir
```

If `--git-dir` equals `--git-common-dir`, you are in the main repo, not a worktree — **stop and tell the user**. Rebasing on the main checkout defeats the entire point of this flow.

The working tree must be clean before rebasing:

```bash
git status --short
```

If it's dirty, commit the pending chunk first (this repo's agents commit incrementally — see `execute-local-worktree` Step 6) or stash it. Never start a rebase with uncommitted work you care about.

### Step 2: Rebase the branch onto latest `main`

```bash
mise run wt:rebase     # rebases onto LOCAL main; self-heals a stale index.lock
```

This replays the worktree's commits on top of the current tip of `main`. Commits get **new hashes** — that's expected; rebase rewrites history:

```
before   main: A─B─C       branch: A─B─C─D─E
after    main: A─B─C       branch: A─B─C─D'─E'   (D',E' = replayed)
```

If the rebase completes with no conflicts, skip to Step 5.

### Step 3: Resolve each conflict

When the rebase stops on a conflict, git leaves markers (`<<<<<<<` / `=======` / `>>>>>>>`) in the affected files and pauses. Inventory and resolve:

```bash
git status            # files marked "both modified" / unmerged
git diff --check      # locate conflict markers
```

For every conflicted file, read both sides and integrate intelligently. **The rule: keep BOTH main's recent work AND the worktree's intent.** Never just pick one side unless you're certain the other is obsolete.

Pay extra attention to:
- **Import / `use` lists** — main may have added imports the worktree's edit hides; preserve both sets.
- **Module / route / command registrations** — registrations added on main since branch-off must survive.
- **Config files** — `Cargo.toml`, `package.json`, `mise.toml`, settings schemas: merge new entries from both sides.
- **Test files** — keep both new tests; never drop one to silence a conflict.
- **Schemas, types, enums, migrations** — additions on main must merge with the worktree's additions, not collide.

After resolving a file, stage it and continue the rebase:

```bash
git add <resolved-file-1> <resolved-file-2>
git rebase --continue        # repeat for each paused step until the rebase finishes
# git rebase --abort         # only if the rebase has gone wrong and you need to start over
```

Repeat until `git rebase` reports the rebase is complete.

### Step 4: Verify no markers remain

```bash
git diff --check
git status --short           # should be clean — no unmerged entries
```

If any markers remain or the rebase is still in progress, return to Step 3.

Confirm the shape — your commits sitting directly on top of latest `main`:

```bash
git log --oneline --decorate --graph --max-count=20
```

### Step 5: Run pre-commit until clean

A clean rebase isn't enough — main's code combined with the worktree's code can break the build or tests. Your work is not done until `mise run pre-commit` exits 0.

1. Run `mise run pre-commit`.
2. Read the failures. They typically fall into:
   - **Typecheck / compile errors** — main introduced a type the worktree's code now needs to match.
   - **Lint errors** — formatting drift or new rules.
   - **Unit tests** — main's logic combined with the worktree's logic surfaced a bug.
   - **Schema / config drift** — generated artifacts need regenerating after a merge.
3. Fix the **root cause** of each failure. Never suppress (blanket `#[allow]`, `@ts-ignore`, `eslint-disable`, `.skip`, deleting failing tests). If you reach for a suppression, stop and find the real cause.
4. Re-run `mise run pre-commit`. New failures may surface after the first batch — that's expected.
5. Repeat until the command exits 0.

If the fixes are non-trivial, commit them as a `fix:` chunk so history stays clean. Trivial resolution touch-ups can ride into the rebase you just performed.

### Step 6: Report

When `mise run pre-commit` exits cleanly, report:

```
Rebase complete

Worktree: <name>
Branch:   <branch>  (rebased onto main @ <short-sha>)
Conflicts resolved: <count>  (or "none — clean rebase")
Files touched in resolution: <count>
Pre-commit: ✅ clean

The branch is now main + the worktree's commits, replayed on top. The human can
fast-forward main to this branch (Stage 8) with no conflicts:
  git switch main && git merge --ff-only <branch>
```

## Critical Rules

- **You DO commit / rebase here** — that's the rebase model. Resolve conflicts, `git add` the resolved files, `git rebase --continue`, and commit any non-trivial fix chunks.
- **NEVER touch `main`.** You rebase the worktree branch *onto* `main`; you never fast-forward, merge into, or push to `main`. That's Stage 8, the human's op.
- **NEVER pick one side wholesale** unless you've verified the other side is obsolete. Both sides exist for a reason.
- **NEVER suppress pre-commit failures.** Fix the root cause.
- **NEVER bypass `pre-commit`** — no `--no-verify`, no disabling hooks.
- **NEVER exit the worktree** when done — leave it for the user to review and land.
- **Stay terse.** Brief progress updates only — "rebasing onto main", "conflict in src/foo.rs, keeping both", "pre-commit failing on type errors in bar.rs, fixing", "clean" — not a play-by-play.
