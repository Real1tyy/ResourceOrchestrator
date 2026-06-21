---
name: change
disable-model-invocation: true
description: Direct-to-main fast path — make the change RIGHT NOW, on the main checkout, no worktree, no spec, no docs, no questions. The inverse of one-shot (which isolates in a worktree). Requires the session to be launched with main edit/commit enabled (scripts/change/change.py). Use when the maintainer says "just do X on main", "change this straight on main", or invokes /change. The maintainer decides scope; if they invoked this, execute it on main. The one thing never skipped is the pre-commit gate — main never ships red.
---

# change — Just Do The Change, On Main, Now

This is the **maximum-speed path**. one-shot already drops the spec/plan/retro
ceremony but still isolates in a worktree. `change` drops the worktree too: you edit
the **main checkout directly** and commit straight to `main` with the *exact same plain
workflow* you'd use in a worktree — `Edit`/`Write`, then `git add` + `git commit`. No
special wrapper, no per-command script.

That works because the session was **launched with the two capability hatches set**
(`scripts/change/change.py`, which is just `AGENTIC_ALLOW_MAIN_EDIT=1
AGENTIC_ALLOW_MAIN_COMMIT=1 claude`). Those env vars are the *whole* mechanism: with
them set, the default-deny policy (spec 0031) allows editing and committing on `main`
exactly as it allows them inside a worktree.

**The maintainer decides scope — not you.** If they invoked `/change`, they have
already decided this belongs on `main`. Do not second-guess by size, do not declare it
"too big," do not redirect to a worktree, do not stop to ask. Execute the change. (You
*may* note in your final report that something was larger than a typical quick edit —
but you still do it.)

## What this skill drops — and the one thing it never drops

Dropped (the whole point):
- **NO worktree.** You work in the main checkout.
- **NO spec, NO plan, NO acceptance criteria, NO retrospective, NO ADR.**
- **NO docs / markdown paperwork.** Don't write any unless the change *is* docs.
- **NO questions.** Do what was asked; resolve small ambiguity with the most
  reasonable reading and note the assumption in your one-line report.

**Never dropped: the pre-commit gate.** Every commit still runs lefthook
(`mise run pre-commit` + the conventional-commit check). `main` never ships red, and
`--no-verify` is forbidden here as everywhere. "Fast" means no ceremony — never broken
code on `main`.

## The loop

### Step 1 — Verify you have the capability

```bash
printenv AGENTIC_ALLOW_MAIN_EDIT AGENTIC_ALLOW_MAIN_COMMIT
git rev-parse --show-toplevel
```

- Both env vars print `1`, and the toplevel is **not** under `.claude/worktrees/` →
  proceed.
- **Either env var is empty/missing** → this session was not launched for direct-to-main
  work. **Stop** and tell the maintainer to relaunch with the launcher:
  `python3 scripts/change/change.py` (or
  `AGENTIC_ALLOW_MAIN_EDIT=1 AGENTIC_ALLOW_MAIN_COMMIT=1 claude`). Without the hatches,
  edits and commits on `main` are blocked by design — don't try to work around it.
- **Toplevel is under `.claude/worktrees/`** → you're in a worktree; this is the wrong
  skill. Use `/one-shot` and work in place.

### Step 2 — Make the change directly

Edit the real files with the `Edit`/`Write` tools. Follow the existing patterns in the
files you touch. **Do not ask the user anything.**

### Step 3 — Write/adjust a test when the change has behavior

A bug fix gets a reproducing test; new behavior gets a test. The gate enforces this and
it's how the maintainer trusts an unreviewed main commit. A pure typo/copy/const tweak
with no behavior needs none — don't invent ceremony.

### Step 4 — Commit straight to main (plain git)

```bash
git add <files>
git commit -m "type(scope): the change"
```

- Conventional Commit message — the commit-msg gate rejects anything else.
- **Never** append attribution trailers (`Co-Authored-By`, "Generated with …").
- If the gate fails, **fix the root cause** and re-run. Never `--no-verify`.

### Step 5 — One-line report. Done.

No hand-off ceremony, no spec to tick, no retrospective. Report what changed and the
commit sha, e.g.:

```
Done on main @ <sha> — <one-line summary>. Gate: ✅. Assumptions: <none|…>.
```

## Critical rules

- **The maintainer owns the scope decision.** If they invoked `/change`, do it on
  `main` — whatever its size. Never redirect to a worktree on size grounds.
- **NEVER ask the user questions.** Resolve ambiguity and note it.
- **NEVER bypass the gate.** No `--no-verify`; fix red, don't skip it.
- **NEVER write a spec, ADR, retrospective, or doc** unless the change itself is that.
- **NEVER run from a worktree** — that's `/one-shot`'s job.
- If the capability env vars aren't set, **stop and ask to be relaunched** — do not try
  to self-enable them mid-session.
