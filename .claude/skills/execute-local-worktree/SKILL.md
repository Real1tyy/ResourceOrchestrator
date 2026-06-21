---
name: execute-local-worktree
disable-model-invocation: true
description: Execute a planned feature/fix in an isolated git worktree using this repo's rebase model (commit incrementally, rebase onto main, hand off). Repo-local; deliberately named distinctly from any personal global `execute-worktree` skill so it is never shadowed. Use after planning or discussing changes — implements everything in a separate worktree, never touching the main working tree. The loop closes by marking the spec done and writing a retrospective.
---

# Execute in Worktree

Implement the planned changes in a new isolated git worktree. This is the "go build it" command — used after a plan has been discussed, a feature has been agreed on, a spec has been written, or the conversation has established what needs to happen.

**One agent → one worktree → one spec → one loop.** Each agent run takes a single unit of work (typically one spec file under `docs/specs/`) into an isolated worktree, iterates until acceptance criteria pass, commits incrementally, rebases onto `main`, self-reviews, marks the spec done, writes a retrospective, and hands off for human review. Don't bundle multiple specs into one worktree; don't skip the close-the-loop steps.

## The worktree lifecycle (where this skill sits)

This repo runs the **rebase model** (see `docs/AGENTS.md` for the full model): agents commit incrementally and run git themselves with plain git; integration is a rebase of the branch onto `main` (in the worktree) followed by a fast-forward of `main` to the branch (the human's op).

This skill is **Stages 1–5 and 7** of the canonical spec-to-merged lifecycle. The full eight stages, with what is **active now** vs **not yet wired up**:

| Stage | What happens | This skill | Status |
|---|---|---|---|
| 1. Assign & plan | Agent is told its worktree + spec; plans before coding | Steps 1–5 | **active** |
| 2. Implement & commit incrementally | One coherent commit per chunk (feature + its tests); `pre-commit` green on each | Step 6 | **active** |
| 3. Rebase onto `main`, resolve conflicts locally | `mise run wt:rebase` inside the worktree (rebases onto **local** `main` — origin is stale here, `worktree.baseRef=head`) so the eventual fast-forward is clean | Step 7 → see [merge-worktree] for conflict discipline | **active** |
| 4. Hand-off checks (`pre-stop`, the repo's local "CI") | `mise run pre-stop` — change-report, every `check` step, then e2e for **only the specs this worktree changed**; `pre-commit` skips e2e entirely. The full e2e suite is human-only. | Step 8 | **active** — run once at the end before hand-off; the Stop hook re-runs it; there is no remote CI, this *is* the CI (local hooks + tasks) |
| 5. Self-review (report-driven) | Change report → review own diff against acceptance criteria → fix | Step 9 | **active** |
| 6. Reviewer agent (parallel) | A separate agent reviews and fixes or feeds back | — | **not yet** — we do not invoke reviewer agents; self-review (Stage 5) is the only review before the human |
| 7. Human in the loop | Human sees report + diff + green `pre-stop` + self-review, then approves | Step 12 hand-off | **active** — agent **stops here and waits**; rebase is already done so approval is clean |
| 8. Integrate & teardown | Final rebase, fast-forward `main` to the branch, remove worktree/branches | the human's op | **human's op** — never run by this agent |

The core invariant: **one agent, one worktree, one-to-one, always.** A worktree is never shared by two agents at once. The 1:1 binding is kept simple and held by convention, not hard-locked — the isolation is what makes parallel agents safe.

## Capability posture: default-deny (spec 0031)

Your Bash capability is **default-deny** — a `PreToolUse` hook (allowlist at `.claude/hooks/policy/allowlist.toml`, engine at `.claude/hooks/validators/command_policy.py`) blocks any command not explicitly allowed and tells you the sanctioned alternative. Operate **inside** that surface; it keeps you focused, not boxed in.

- **Lifecycle operations go through `wt:*` Mise tasks**, never raw plumbing:
  - `mise run wt:new <slug>` — create the worktree (instead of `git worktree add`); auto-runs `wt:setup` (install deps + format the whole tree) so `pre-commit` is runnable on your very first commit.
  - `mise run wt:rebase` — rebase the branch onto `main` (instead of raw `git fetch`/`git rebase`).
  - `mise run pre-commit` — the fast per-commit checks (also run automatically by the commit hook); run it by hand to validate before committing.
  - `mise run wt:verify` — self-review inputs (diff vs merge-base, report when it ships).
  - `mise run change-report` — generate a change-report for the worktree diff.
  - `mise run pre-stop` — the heavy hand-off checks (change-report + all checks + changed-spec e2e); runs automatically via the Stop hook, but you can run it manually to validate before stopping.
- **Incremental git inside your worktree is allowed and expected** — `git add`, `git commit`, `git status`/`log`/`diff`, `git rebase --continue`, `git stash`, etc. all pass *because you are in a worktree under `.claude/worktrees/`*. The rebase model is unchanged.
- **File edits must land inside your worktree.** `Write`/`Edit`/`MultiEdit`/`NotebookEdit` against a path in the **main checkout** (outside `.claude/worktrees/`) are **blocked** by the hook — the file-tool analogue of no-commit-on-main. This is the single most common drift: enter your worktree *first* (Step 4) and edit only paths under `.claude/worktrees/<your-worktree>/`. If you see this block, you skipped entering the worktree.
- **What's blocked** (the hook names the alternative each time): committing from the **main checkout** (enter a worktree first), `git push --force` (use `--force-with-lease`), `git reset --hard`, `git branch -D`, `git clean -f`, `--no-verify`, `mise run wt:land` (that's the human's Stage-8 op), and any command not on the allowlist. If you hit a block, read the message and use the named task — don't try to route around it.
- Need a capability that isn't allowed? That's a signal to add it to the allowlist (a deliberate change), not to find a workaround.

Integration is two distinct Git operations, **in this order**, and only the first is in scope for this skill:

1. **Rebase the worktree branch *onto* latest `main`** (inside the worktree — Stage 3, this skill).
2. **Fast-forward `main` *to* the worktree branch** (Stage 8 — the human's op).

There is no "rebasing from the worktree to `main`." You **rebase the branch onto `main`**, then the human **fast-forwards `main` to the branch**.

## Input

The user may provide:
1. **A spec reference** (e.g., a file path under `docs/specs/`, a spec id like `spec-my-feature`, or full spec contents pasted in) — the preferred form for spec-driven repos.
2. **Description or worktree name** (optional) — what to implement, or a specific worktree name to use.

If nothing is provided, look at the current conversation history for a plan or agreed-upon task.

## Instructions

### Step 1: Determine What to Implement (one spec, one worktree)

**Start here — before touching git.** There is no point entering a worktree until you know what you are building.

- If the user supplied a spec (file path, spec id like `spec-user-auth-flow`, or pasted content), use that as the single source of truth. **One spec per worktree** — never bundle two.
- If the user provided a description without a spec, use the description but note in the hand-off that there's no spec file to mark done.
- If no input AND no prior conversation establishing what to do, **STOP and ask the user**. Do not guess.

The repo uses `worktree.baseRef = head` (see `docs/AGENTS.md`), so a new worktree inherits local `HEAD` — including the spec file and any in-progress local commits. If you ever find the spec is missing inside the worktree, create it at the correct path so Step 11 can mark it done locally, and flag it in the hand-off.

### Step 2: Choose a Worktree Name

The worktree name is derived directly from the spec id — this makes the spec↔worktree pair
unambiguous in `git worktree list`, `mise run wt:list`, and the hand-off summary.

**Derivation rule:** strip the `spec-` prefix from the spec id.

| Spec id | Worktree name |
|---|---|
| `spec-user-auth-flow` | `user-auth-flow` |
| `spec-sqlite-migration` | `sqlite-migration` |
| `spec-fix-login-redirect` | `fix-login-redirect` |

- If the spec id is not yet known (freeform description, no spec file), use conventional
  commit style: `type-short-description` (`feat-calendar-export`, `fix-login-redirect`).
- If the user explicitly provided a worktree name, use that instead.

The name travels with the branch (`worktree-<name>`), the directory
(`.claude/worktrees/<repo>--<name>--<date>`), and the hand-off summary — keep it the
same slug all the way through.

### Step 3: Check Your Branch — and Detect Your Entry Mode

Now check the current branch **and** working directory:

```
git rev-parse --abbrev-ref HEAD
pwd
```

There are **two entry modes**, and they diverge at Step 4:

- **From `main` (the common case — invoked to start fresh).** If you are on `main` or `master`, you **MUST** create and enter a worktree before writing any code (Step 4). Do NOT proceed to implementation without entering a worktree first. This is the single most important rule.
- **Already inside a worktree (the daemon's one-click Execute mode).** If you are **NOT** on `main`/`master` **and** your `pwd` is under `.claude/worktrees/<…>`, you are already inside the dedicated worktree the daemon spawned for you. Do **NOT** create or enter a second worktree — that would nest one. **Skip Step 4** and continue at Step 5, using this worktree and its branch as-is.

When in doubt (detached HEAD, an unusual cwd), prefer the create path.

### Step 4: Enter the Worktree (skip if already inside a worktree — see Step 3)

- Use the `EnterWorktree` tool with the chosen name.
- **NON-NEGOTIABLE:** All implementation work MUST happen inside the worktree. Do NOT modify any files in the main working tree. If you have not entered a worktree, do NOT start writing code.
- **`EnterWorktree` sets a persistent cwd — use it, don't re-`cd`.** After entering, every Bash call already runs in the worktree, so prefixing commands with `cd <worktree> && …` is wasted. Run commands bare; use absolute paths for file tools.

### Step 5: Read Project Conventions

Before implementing:
- Read `CLAUDE.md` if it exists — follow all project rules. Pay special attention to any "Spec-Driven Worktree Loop" or similar section that encodes how the project expects this skill to behave.
- Read the spec file end to end. Acceptance criteria are the contract.
- Scan for testing conventions, linting config, build commands, and the **commit convention**. This repo enforces Conventional Commits via a `commit-msg` hook and runs the fast checks via the `pre-commit` framework.
- Understand the project structure and patterns.

**`wt:new` already bootstrapped the worktree for you.** When you created the worktree, `wt:new` ran `mise run wt:setup` automatically (install deps + format the whole tree), so `pre-commit` is runnable on your first commit. If you are already inside a pre-existing worktree that was not created via `wt:new`, run `mise run wt:setup` now before your first commit.

### Step 6: Implement & Commit Incrementally (Stage 2)

Implement the spec in **coherent chunks**, committing each one as it goes green. Incremental commits give clean diffs and clean history, and `pre-commit` gives fast feedback on every chunk. **You run git yourself** — that is the rebase model.

The loop, per chunk:

1. Implement one coherent unit of work — and its tests (tests-first where the project follows TDD).
2. Stage and commit that chunk with a Conventional Commit message:
   ```bash
   git add <files for this chunk>
   git commit -m "feat(scope): implement first chunk"
   git add <test files>
   git commit -m "test(scope): add coverage for first chunk"
   ```
   Commit often — one coherent commit per chunk. If a spec needs three features, that's roughly feature 1 (+tests), feature 2 (+tests), feature 3 (+tests). The `commit-msg` hook rejects non-conventional messages; the pre-commit hooks run on each commit.
3. **`pre-commit` runs on every commit** (`mise run pre-commit` / the hook chain — lint, test, build, type-check). Iterate on the chunk until it passes, *then* move to the next chunk. Each chunk green before the next.
4. Cross-reference the spec's Acceptance Criteria after each chunk to stay on scope.

Never bypass `pre-commit` (`--no-verify`, disabling hooks). If `pre-commit` fails on a **pre-existing** issue your diff does not touch (e.g., the base ref was already broken), do not silently fix it — call it out in the Step 12 hand-off and leave the broken file for a separate fix.

### Step 7: Rebase onto `main` & Resolve Conflicts Locally (Stage 3)

With all chunks committed, rebase the worktree branch onto the latest `main` **inside the worktree**, so conflicts are resolved here — before any human looks at it. When the human later fast-forwards `main` to this branch (Stage 8), it's a pure fast-forward with nothing to negotiate.

Working tree must be clean first (`git status --short`). Then, inside the worktree:

```bash
mise run wt:rebase     # rebases onto LOCAL main (origin is stale here); guides conflicts, self-heals a stale index.lock
```

(A raw `git rebase origin/main` is **denied** — `origin` is routinely stale under `worktree.baseRef=head`, so it rebases onto the wrong ref; the redirect points you here. `git rebase main` and `git rebase --continue`/`--abort` stay available for hand conflict resolution.)

If conflicts surface, resolve each one keeping **both** main's recent work and the worktree's intent — never blindly pick a side (this is the [merge-worktree] conflict discipline, done in-worktree during the rebase):

```bash
git status                 # conflicted files
# resolve each file by hand
git add <resolved-file>
git rebase --continue      # repeat until done
# git rebase --abort       # only if the rebase goes wrong
```

Rebase rewrites commit hashes — verify the shape afterward (your commits sitting directly on top of latest `main`):

```bash
git log --oneline --decorate --graph --max-count=20
```

**Race window is accepted — and the auto-rebaser closes it for you.** `main` can move again between this rebase and the human's eventual merge. That window is small; the human re-rebases at Stage 8. Once you hand off on a clean tree, any watch-main daemon keeps your branch current. **Don't fight it:** rebase **once** here, then stop. Do **not** loop re-running `wt:rebase` to chase a `main` that keeps moving, and never poll or race an `index.lock` — a git op is in flight, and the concurrency guard blocks that race by design. A branch briefly reading "behind `main`" is normal and reconciled, not a problem to chase.

### Step 8: Validate before hand-off (`pre-stop`)

The Stop hook runs `mise run pre-stop` automatically when the session ends — it also verifies the rebase check. You don't have to remember to invoke it, but **run it yourself at the end of the loop** to get the green before stopping (the Stop hook then replays the cached green in ~0 s on an unchanged tree):

```bash
mise run pre-stop   # change-report + all check steps + changed-spec e2e; runs automatically on stop anyway
```

`pre-stop` is the heavy hand-off run: it runs `change-report`, then all `check` steps, then the Playwright e2e for ONLY this worktree's changed specs, and stamps a `gate_type: pre-stop` telemetry record on green. There is no `PreStop` hook event — `pre-stop` is the *task*; the *invocation* is the `Stop` hook. The full e2e suite is **blocked for agents** (human-only, before a push) — never run it.

If a step fails, iterate that step alone — `mise run check:security`, `mise run test:e2e:changed`, `mise run check:backend` — fix it, re-commit, and stop again (the hook re-runs `pre-stop`).

### Step 9: Self-Review (Stage 5)

`pre-stop` generates the change report automatically (via `mise run change-report`). Read it and apply the `worktree-review` lens:

- Check which files need real review (`code` kind) vs skimmable (`formatting`/`comment`/`import`).
- Check for unintended Public API surface changes.
- **Sanity-check against the spec's acceptance criteria:** every criterion maps to a real change in your diff; every change maps to a criterion. A criterion with no matching change means the work isn't done. A change with no criterion means you've drifted out of scope.

If the project has no change-report tool yet, fall back to summarizing `git diff` against the spec criteria.

Fix anything the review surfaces. Commit the fix, then let the stop hook re-run `pre-stop`.

### Step 10: Mark the Spec Done

(Skip if there's no spec file — i.e., the task was a freeform description.)

Edit the spec file at `docs/specs/<YYYY-MM-DD-HHMMSS>-<slug>.md` **inside the worktree**:

- Set `status: accepted` (or `superseded` / `archived` if the work obsoletes the spec).
- Bump `updated:` to today's date.
- Tick every satisfied `- [ ]` checkbox under **Acceptance criteria** → `- [x]`. If any criterion is **not** satisfied, leave it unticked, set `status: proposed` (not `accepted`), and call out the gap in the hand-off.
- Add an `## Outcome` section at the bottom summarizing what landed (file list, key decisions) and linking to the retrospective from Step 11.
- Resolve `## Open questions` that the work actually answered; leave the rest.

Commit the spec update as its own `docs(spec):` chunk.

### Step 11: Retrospective (two feedback channels)

The retrospective is **not** a summary of what was built (that goes in the spec's
`## Outcome`). It serves two equal purposes — see `docs/DOCS.md §7` for the full
convention. One section per distinct finding, labelled `[Code]` or `[Process]`.
Keep every finding actionable; if it doesn't point at a concrete improvement, skip it.

Create it with `mise run docs:new memory --json '{"name": "<spec-slug>", "body": "<findings>"}'`
(a direct `Write` of a new doc is blocked by the hook; the tool stamps the unique
`docs/memory/<YYYY-MM-DD-HHMMSS>-<spec-slug>.md` + frontmatter, then `Edit` to refine).
**Skip when the run was genuinely routine** — no surprises, no friction, no lessons worth
carrying forward.

**[Code] channel — what did we learn about the application?**
- **Bugs caught:** document root cause + the regression test that now prevents
  recurrence. Every bug only happens once.
- **Architectural flaws / wrong abstractions:** name the mismatch and what a better
  approach looks like. Input for a future ADR or refactor spec.
- **Pattern violations:** if the agent used an anti-pattern because the right pattern
  wasn't documented — and found out only after a `pre-commit`/`pre-stop` failure, reviewer
  comment, or runtime crash — that is a documentation gap. Name the pattern and propose
  enforcement (lint rule, check, CLAUDE.md addition). The right fix is enforcement, not just prose.
- **Test gaps:** anything that broke because there was no test for it.

**[Process] channel — what made the loop slow, hard, or uncertain?**
- **Time bottleneck:** name the specific step with timing. The bottleneck is the
  highest-ROI target for optimization. (E.g. "unit suite 15s × 40 commits = ~10 min;
  `test:unit:changed` would cut to 5 runs.")
- **Missing context:** what did you have to discover mid-sprint that CLAUDE.md, the
  spec, or a seeded doc should have told you upfront?
- **Tooling friction:** slow/flaky checks, blocked commands, confusing error messages.
  Each is a concrete improvement target.
- **Automation proposals:** steps that could be scripted, cached, or seeded as
  no-op defaults the next agent never has to think about.

Commit the retrospective alongside the spec update (Step 10).

### Step 12: Hand Off (Stage 7 — human in the loop)

When the chunks are committed, the branch is rebased onto `main`, you've self-reviewed, the spec is marked done, the retrospective is written, **and `mise run pre-stop` (change-report + check steps + changed-spec e2e) is green on that final state (Step 8)** — **stop and hand off.** Do not fast-forward `main`, do not push to `main`, do not tear down. The human reviews the full context at once (diff + green `pre-stop` + self-review) and runs Stage 8 themselves.

```
Worktree Implementation Complete — ready for human review

Worktree: <worktree-name>
Branch:   <branch-name>  (rebased onto main @ <short-sha>)
Spec:     docs/specs/<YYYY-MM-DD-HHMMSS>-<slug>.md  →  status: accepted
Commits:  <n> incremental commits (chunk + tests)
Files:    <count> changed, <count> added
Checks:   pre-stop ✅ (change-report + check steps + changed-spec e2e)  | per-commit: pre-commit ✅
Rebase:   clean onto main  | <or: "conflicts resolved in <n> files">

Self-review (no reviewer agent — this is the only review before you):
  <homogeneity headline — e.g. "3 files need real review; 11 skimmable">
  <code-kind files the user should focus on>
  <any API-surface changes; confirm they match the spec>

Acceptance criteria:
  [x] criterion 1
  [x] criterion 2
  ...

Retrospective: docs/memory/<YYYY-MM-DD-HHMMSS>-<spec-slug>.md
Change report: logs/change-reports/worktree-<name>.md  (if generated)

Next (your op — Stage 8): final rebase if main moved, then fast-forward main to
this branch and tear down:
  git switch main
  git merge --ff-only <branch-name>
  git worktree remove .claude/worktrees/<worktree-name>
  git branch -d <branch-name>

To inspect first:
  cd .claude/worktrees/<worktree-name>
  git log --oneline --graph main..HEAD
  git diff main
```

## Critical Rules

- **NEVER modify the main working tree.** All changes go in the worktree. No exceptions.
- **NEVER start coding before entering the worktree.** The `EnterWorktree` tool MUST be called first.
- **COMMIT INCREMENTALLY** — one coherent commit per chunk, Conventional Commit messages, `pre-commit` green on each (Step 6). Running git yourself is the expected flow.
- **`mise run wt:new` bootstraps the worktree automatically** — `wt:setup` runs on creation (install deps + format the whole tree), so `pre-commit` is runnable on the first commit. If you are in a pre-existing worktree not created via `wt:new`, run `mise run wt:setup` before the first commit.
- **NEVER bypass `pre-commit`** — no `--no-verify`, no disabling hooks, no suppressing failures. Fix the root cause.
- **REBASE THE BRANCH ONTO `main`** inside the worktree and resolve conflicts locally (Step 7) — so the human's fast-forward is clean. You rebase the branch onto `main`; you never rewrite `main` itself.
- **NEVER fast-forward `main`, push to `main`, or tear down the worktree.** Stage 8 is the human's op. Stop at hand-off.
- **NEVER invoke a reviewer agent** — that stage isn't wired up. Self-review (Step 9) is the only review before the human, so do it thoroughly.
- **The Stop hook runs `mise run pre-stop` for you** — it verifies the rebase and runs the heavy hand-off checks automatically. Run `mise run pre-stop` manually to validate ahead of time. **NEVER run the full e2e suite** — it is human-only and will mine the CPU; the allowlist blocks it.
- **NEVER skip the close-the-loop steps** (Step 10: mark spec done; Step 11: retrospective). They're the difference between a worktree that taught the next agent something and one that didn't.
- **NEVER silently fix unrelated pre-existing `pre-commit`/`pre-stop` failures.** Call them out and leave them.
- **ALWAYS self-review against the spec's acceptance criteria** before hand-off (Step 9). Every criterion maps to a change; every change maps to a criterion.
- **ALWAYS follow ALL existing project rules** — CLAUDE.md, changelog, docs, testing, versioning, whatever the repo requires.
