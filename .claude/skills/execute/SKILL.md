---
name: execute
disable-model-invocation: true
description: Execute mode — the midpoint between /planner (spec only) and /one-shot (code only). In ONE worktree, draft a convention-compliant spec, get the maintainer's sign-off (the default checkpoint), then implement it through the full pipeline — commit incrementally, rebase onto main, green `pre-commit`/`pre-stop`, mark the spec done, hand off. The opening prompt may carry an "autonomous mode" directive that drops the sign-off checkpoint (commit the spec and proceed straight to execution). Use when the user says "spec it and build it", "plan then execute", or invokes /execute.
---

# Execute — Spec, then Build, in One Worktree

This is the **Execute** mode of the prompt-spawn family. It sits between the two
extremes:

- `/planner` produces a spec and **stops** (no code).
- `/one-shot` writes code and **never** produces a spec.
- **`/execute` does both, in the *same* worktree:** draft a spec, (by default) wait for
  the maintainer to sign off, then implement that spec end-to-end through the full
  pipeline and hand off.

The deliverable is one spec file under `docs/specs/` **and** its implementation —
committed incrementally, with the spec marked `accepted` and its acceptance criteria
ticked once the work is green.

## The sign-off checkpoint (read this first)

By **default** you **wait for human sign-off on the spec before implementing it.** Draft
the spec, present it in the conversation, and **stop for the maintainer to approve or
redirect** — exactly as the Planner does. Only after they confirm do you commit the spec
and start coding.

**Autonomous override.** If the opening prompt contains an **"Autonomous mode"** line
(the maintainer cleared "wait for sign-off" in the spawn modal), **do not wait**: commit
the drafted spec yourself and proceed straight into implementation without pausing for
approval. The maintainer reviews the finished diff at hand-off instead. When in doubt
about which mode you're in, the directive in the opening prompt is authoritative — its
presence means autonomous, its absence means wait.

Either way, everything downstream of the spec (implement, green the checks, mark done,
hand off) is identical. The toggle only decides whether you pause between drafting and
building.

## Capability posture: default-deny (spec 0031)

Your Bash/file capability is **default-deny** — a `PreToolUse` hook (allowlist at
`.claude/hooks/policy/allowlist.toml`) blocks any command not explicitly allowed and
**blocks `Write`/`Edit` whose target is in the main checkout** (outside
`.claude/worktrees/`). All work — the spec file *and* the code — happens inside your
worktree. If a write is blocked, you are not inside the worktree.

- **Reading is unrestricted** — `ls`, `cat`, `rg`, `git status/log/diff`, and
  `python3 scripts/specs.py` run against the main checkout for catch-up.
- **Lifecycle ops go through `wt:*` tasks** — `mise run wt:new`,
  `wt:rebase`, `wt:verify`. Never raw `git worktree add`. `mise run wt:land` is the
  human's op — off your allowlist; **never land.**
- **Incremental git inside the worktree is allowed** — `git add/commit/status/log/diff`,
  `git rebase --continue`, etc. Running git yourself is the rebase model.

## The loop

### Step 1 — Ensure you are in a worktree

```bash
git rev-parse --abbrev-ref HEAD
pwd
```

- **Already inside `.claude/worktrees/<…>` on a non-`main` branch** (the daemon's
  Execute button always lands you here) → **work in place.** Do NOT create a second
  worktree — that nests one. Run `mise run wt:setup` once if deps aren't installed.
- **On `main`/`master`** → `mise run wt:new <slug>` (a kebab slug from the task), then
  enter it (the `EnterWorktree` tool). All work happens inside it.

When in doubt (detached HEAD, unusual cwd), prefer the create path.

### Step 2 — Catch up on conventions, then draft the spec

Before writing the spec, load the repo's spec machinery so the draft is
convention-correct:

- **`CLAUDE.md`** — the *Spec-Driven Worktree Loop* and *Documentation Conventions*.
- **`docs/DOCS.md` §3 (Frontmatter Contract) and §5 (Spec Conventions)** — the
  frontmatter fields, the WSJF `value`/`difficulty` deck, and the **9 required
  sections**.
- **`docs/specs/_template.md.tmpl`** and a couple of recent accepted specs for house
  style.
- Scan `docs/specs/` and `docs/decisions/` for overlap/related work — surface a near
  duplicate rather than silently creating one.

**Align before you draft — but to a point.** Unlike `/planner`, you are heading
straight into implementation, so don't run the full multi-round interrogation. Still:
if the prompt leaves *real* ambiguity in **scope or acceptance criteria**, ask the
maintainer a focused round or two with `AskUserQuestion` before drafting — capture
enough to make the acceptance criteria sharp, then draft. Get aligned on the
*contract*, not every implementation detail. **Exception — autonomous mode:** if the
opening prompt carried the "Autonomous mode" directive, skip the questions entirely and
draft from the best reasonable interpretation, noting any assumption in the spec.

Draft **one** spec covering this task (Execute is one-spec-per-worktree — if the idea
genuinely splits into several units of work, that's a `/planner` job; say so and stop).
The **acceptance criteria are the contract you'll implement against** — make them
testable end-states, not implementation steps.

### Step 3 — Sign-off (default) OR commit straight through (autonomous)

**Default (wait):** Present the complete drafted spec — full frontmatter + the 9
sections — in the conversation and **stop for the maintainer's approval.** Iterate in
chat until they sign off. This is the last cheap place to change direction.

**Autonomous (opening prompt carried the directive):** skip the pause entirely.

Once approved (or immediately, in autonomous mode), **create** the spec with
`mise run docs:new spec --json '{"name": "<short title>", "value": N, "difficulty": N,
"tags": [...]}'` **inside the worktree** — it scaffolds the unique
`docs/specs/<YYYY-MM-DD-HHMMSS>-<slug>.md`, the `<type>-<slug>` id, and the section
skeleton (a direct `Write` of a new doc is blocked by the hook → use this tool; then
`Edit` the file to fill the sections and set `status: proposed`). Commit it as its own
`docs(spec): add <short title>` chunk.
`pre-commit` runs on the commit; green it (markdown/spelling linters included) before
moving on.

### Step 4 — Implement the spec incrementally

Now build it — this is the `/execute-local-worktree` discipline, in this same worktree:

- Implement in **coherent chunks**, committing each as it goes green (feature + its
  tests). Conventional Commit messages; `pre-commit` runs on every commit.
- Tests-first where the repo follows TDD; every new behavior gets a test, every bug fix
  a reproducing test.
- Cross-reference the spec's acceptance criteria after each chunk to stay on scope.
- **Never** `--no-verify`. **Never** append attribution trailers.

### Step 5 — Rebase onto `main`

With the tree clean (`git status --short`):

```bash
mise run wt:rebase     # rebases onto LOCAL main; guides conflicts
```

Resolve conflicts keeping both sides' intent, re-green `pre-commit`. Rebase **once** — don't
chase a moving `main`; the watch-main daemon keeps an idle, clean branch current. Never
poll or race an `index.lock`.

### Step 6 — Run the pipeline (the repo's local "CI")

```bash
mise run pre-stop   # every check step, then e2e for ONLY the specs this worktree changed
```

Get it green on the final committed state. The full suite (`ci-full`) is human-only —
never run it. Report which changed specs the e2e ran (your new spec qualifies).

### Step 7 — Mark the spec done

Edit the spec file **inside the worktree**:

- Set `status: accepted` (or `superseded`/`archived` if the work obsoletes it).
- Bump `updated:` to today.
- Tick every satisfied `- [ ]` → `- [x]`. If any criterion is **not** met, leave it
  unticked, keep `status: proposed`, and call out the gap at hand-off.
- Add an `## Outcome` section summarizing what landed.

Commit the spec update as its own `docs(spec):` chunk.

### Step 8 — Retrospective (only if it was hard)

If the run surfaced something worth carrying forward — a non-obvious bug, a missing
pattern, tooling friction — create it with `mise run docs:new memory --json '{"name":
"<spec-slug>", "body": "<findings>"}'` (a direct `Write` of a new doc is blocked; the tool
stamps the unique `docs/memory/<YYYY-MM-DD-HHMMSS>-<spec-slug>.md` + frontmatter, then
`Edit` to refine) per `docs/DOCS.md §6`, one actionable finding per section labelled
`[Code]` or `[Process]`. Skip it for a routine run; no empty "nothing surprising" notes.

### Step 9 — Hand off (never land)

Stop and summarize. Do **not** fast-forward `main`, push to `main`, or tear down —
landing is the human's op.

```
Execute Complete — ready for human review

Worktree: <worktree-name>
Branch:   <branch-name>  (rebased onto main @ <short-sha>)
Spec:     docs/specs/<YYYY-MM-DD-HHMMSS>-<slug>.md  →  status: accepted
Mode:     <wait-for-signoff (approved in chat) | autonomous>
Commits:  <n> incremental commits (spec + chunks + tests)
Files:    <count> changed, <count> added
Pre-stop: ✅ (changed specs: <slug>)  | rebase: clean onto main

Acceptance criteria:
  [x] criterion 1
  [x] criterion 2

What landed: <one-paragraph summary>
Retrospective: docs/memory/<…>.md  (or "none — routine run")

Next (your op): review the diff, then fast-forward main to this branch and tear down:
  git switch main
  git merge --ff-only <branch-name>
  git worktree remove .claude/worktrees/<worktree-name>
  git branch -d <branch-name>
```

## Critical rules

- **DRAFT a spec first, then implement it** — both in the *same* worktree. Never skip the
  spec (that's `/one-shot`); never stop at the spec (that's `/planner`).
- **WAIT for sign-off by default.** Only skip the pause when the opening prompt carries
  the explicit "Autonomous mode" directive.
- **ONE spec per worktree.** If the idea is more than one unit of work, hand it to
  `/planner` to split — don't bundle.
- **ACCEPTANCE CRITERIA ARE THE CONTRACT.** Testable end-states; implement against them
  and tick them only when truly satisfied.
- **NEVER skip `pre-stop`.** Green `mise run pre-stop` before hand-off; no `--no-verify`.
- **NEVER land.** No fast-forward of `main`, no push to `main`, no teardown — the human
  lands.
- **ALWAYS work inside the worktree**, commit incrementally with Conventional Commits, no
  attribution trailers.
