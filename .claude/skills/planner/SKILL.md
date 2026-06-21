---
name: planner
disable-model-invocation: true
description: Planner mode — collaboratively turn a raw idea into a finalized, convention-compliant spec under docs/specs/. The agent catches up on this repo's spec conventions, interrogates you with structured follow-ups and AskUserQuestion quizzes to get the full idea onto paper, detects duplicate/related specs and ADRs, proposes WSJF estimates, drafts the spec for your sign-off, then lands it in a dedicated worktree (commit + hand off). Planning-only — it stops at the spec; execution is /execute-local-worktree. Use when the user says "plan a spec", "let's brainstorm a spec", "I have an idea for…", "draft a spec for…", or invokes /planner.
---

# Planner — Idea → Spec

This is the **Planner** half of the Planner/Execute two-mode wedge. Its job is to extract a fully-formed idea out of the user's head and land it as one or more finalized, convention-compliant **specs** under `docs/specs/` — all written into a **single planning worktree**. Each spec then becomes a unit of work `/execute-local-worktree` picks up later (one execution worktree per spec).

**One conversation → one planning worktree.** A session usually yields a single spec, but when the idea genuinely splits into several related specs, write them **all into the same worktree** — never spin up a worktree per spec (they're just markdown files; a worktree apiece is pure overhead). Planning does not split per spec; **execution** does (one execution worktree per spec, later). This skill is **planning-only**: it writes the spec(s) and stops. It does **not** implement, does **not** spawn an execution worktree for the *work*, does **not** tick acceptance criteria. Marking a spec `accepted`, writing code, and closing the loop are Execute-mode's job (`/execute-local-worktree`).

The deliverable is one or more markdown spec files, written into a **single dedicated worktree** (because the default-deny hook blocks the agent from editing the main checkout — see Capability posture), each committed as its own `docs(spec):` change, and handed off together for the user to land in one op.

## Operating posture (read this before brainstorming)

The spec you produce is a contract the next agent executes against — its **acceptance criteria are the most important thing you write.** Vague criteria produce vague work. Spend the conversation getting them sharp.

Interrogation style for this run: **collaborative — guided but gentle.** Ask structured follow-ups, fill the gaps in the required sections, surface trade-offs and alternatives the user may not have considered, and flag where the architecture seems to pull against existing ADRs — but **defer to the user's stated direction** rather than relitigating it. You are a thinking partner pulling the full idea onto paper, not an adversary. When the user has decided, capture the decision (and, if relevant, the rejected alternative) and move on.

**Keep interviewing until you are aligned — do not draft from a thin prompt.** The interview *is* the job, not a preamble to it. Work the questions in **rounds**: ask a batch (`AskUserQuestion`), integrate the answers, then ask the follow-ups those answers expose, and repeat until every required section is filled and you and the maintainer genuinely agree on **scope and acceptance criteria**. A round that surfaces a new unknown means you are not done — loop again. Only stop interviewing when another round would yield nothing new. The opening prompt the harness seeds reinforces this; treat reaching real alignment as the checkpoint that lets you proceed to the draft.

## Capability posture: default-deny (spec 0031)

Your Bash/file capability is **default-deny** — a `PreToolUse` hook (allowlist at `.claude/hooks/policy/allowlist.toml`) blocks any command not explicitly allowed and **blocks `Write`/`Edit` whose target is in the main checkout** (outside `.claude/worktrees/`). That is *why* the finished spec is written inside a worktree, not straight into `docs/specs/` on `main`:

- **Reading is unrestricted** — `ls`, `cat`, `rg`, `grep`, `git status/log/diff/show`, and `python3 scripts/specs.py` all run on the main checkout. All the catch-up and duplicate-detection work (Steps 1–4) happens here, before any worktree exists.
- **The spec file(s) are created inside one worktree** — `mise run wt:new <name>` first, then create each spec with `mise run docs:new spec --json '{"name": "<title>", ...}'` *inside* `.claude/worktrees/<name>/` (it scaffolds the unique `docs/specs/<YYYY-MM-DD-HHMMSS>-<slug>.md` + id + section skeleton; a direct `Write` of a new doc is blocked by the hook → use the tool, then `Edit` to fill). All specs from one session share this single worktree.
- **Lifecycle ops go through `wt:*` tasks** — `mise run wt:new`, `wt:rebase`. Never raw `git worktree add`. `mise run wt:land` is the human's op — off your allowlist.
- If a write is blocked, you skipped entering the worktree. Enter it first.

## Inputs

The user provides a raw idea in any form — a sentence, a paragraph, a pasted design note, a frustration ("X keeps breaking"), or just "I want to plan something." If they gave almost nothing, that's expected: the conversation is how the idea gets extracted. If they reference an existing spec to extend or supersede, treat that as the anchor.

## The loop

### Step 1 — Catch up on conventions (read-only, do this first)

Before asking the user anything substantive, load this repo's spec machinery so your questions and the eventual draft are convention-correct. Read:

- **`CLAUDE.md`** — the working conventions, especially the *Spec-Driven Worktree Loop* and *Documentation Conventions* sections.
- **`docs/DOCS.md` §3 (Frontmatter Contract) and §5 (Spec Conventions)** — the frontmatter fields, the WSJF `value`/`difficulty` deck, and the **9 required sections**.
- **`docs/specs/_template.md.tmpl`** — the canonical skeleton.
- **Two or three recent, accepted specs** — to match house style, depth, and how acceptance criteria are phrased. Don't read all of them now; you'll scan the full set in Step 3.
- Run **`python3 scripts/specs.py`** — the live ranked backlog (ready / blocked / WSJF + dependency tree). This is the source of truth for what exists and what's prioritized.

If `docs/` has a nested override closer to the relevant subsystem (cascading doc lookup, CLAUDE.md), the nearest one wins — but specs always live at repo `docs/specs/`.

### Step 2 — Extract the idea (the brainstorming core)

Turn the raw idea into the **9 required sections** (DOCS.md §5): **Problem, Goals, Non-goals, User workflow, Requirements, Acceptance criteria, Test strategy, Risks, Open questions.** Drive the conversation section by section, but loosely — follow the user's energy, don't interrogate in rigid order.

Techniques:

- **Use `AskUserQuestion` for the quizzes and questionnaires** the user wants — multiple-choice for genuine forks: scope boundaries ("is X in or out of this spec?"), architecture trade-offs (with the options as previews), which subsystem/crate this belongs in, what the non-goals are. Reserve free-form chat for open-ended discovery. Batch related questions (up to 4 per call) rather than drip-feeding.
- **Pin down acceptance criteria hardest.** They are the contract. Each criterion must be a *testable end-state* ("X endpoint returns Y for input Z"), not an implementation step. Acceptance criteria describe *what must be true at the end*, never *how to get there*.
- **Separate Goals from Non-goals explicitly.** A clear non-goal prevents the next agent from scope-creeping. Ask "what is tempting to include here but should be a *separate* spec?"
- **Surface trade-offs and alternatives** as you go — especially anywhere the idea seems to pull against an existing ADR or architectural decision. Note the chosen path and the rejected alternative; the spec records both. Then defer to the user's call.
- **Cross-reference ARCHITECTURE.md and PRD.md** (or their equivalents in this repo) for the right crate boundaries, source-of-truth rules, and phase placement. A spec that lands code in the wrong module is wrong by construction.
- **Right-size the spec.** If the idea is clearly more than `difficulty: 13` of work, say so and propose a split into multiple specs — no single spec bundles multiple units of work. Plan **all** the resulting specs in this session and write them as sibling files in the **one** worktree (planning shares a worktree; execution still takes one worktree per spec). If you genuinely only want to plan the first now, note the rest as follow-up specs in Open questions.

### Step 3 — Detect duplicates & related work (do this before finalizing)

Read across the **existing specs and ADRs** to place this idea in the landscape — so you can tell the user "we already did something like this here" or "this is related to X" *before* writing anything:

- Scan `docs/specs/` (titles, Problem sections, tags) for **overlap**. If something substantially overlaps, surface it and ask the user to decide: is this a **new** spec, an **extension** of the existing one, or a **superseder**? Don't silently create a near-duplicate.
- Scan `docs/decisions/` for **ADRs that constrain or relate** to this idea — these become `related_docs` and get referenced inline in the spec body.
- Identify **prerequisite specs**: anything that must be `accepted` before this work is runnable becomes `depends_on` (the slug-form id, `spec-<slug>`, matching how `scripts/specs.py` resolves the dependency tree).
- Present the related set back to the user in plain language: "This overlaps with spec X; it's constrained by ADR Y; it depends on Z being done first." Let them confirm or redirect.

Note on identity: a spec's real id is **`spec-<slug>`** in frontmatter. Duplicate numbers are cosmetic — the slug is canonical.

### Step 4 — Propose WSJF estimates

Propose `value` and `difficulty` on the planning-poker deck (**only** `0.5, 1, 2, 3, 5, 8, 13`), with a one-line rationale each:

- `value` = cost-of-delay: how much it hurts to *not* have this; a spec that unblocks others scores high.
- `difficulty` = job size including its tests and keeping `pre-commit` green. **`> 13` is not allowed — split the spec** (back to Step 2).
- Never write a `priority:` field — priority is computed (`value ÷ difficulty`) by `scripts/specs.py`.

Get the user's buy-in or adjustment. Estimates are cheap to revise later.

### Step 5 — Draft the spec(s) for sign-off (in chat, before any file write)

Assemble each **complete spec in the conversation** — full frontmatter + all 9 sections — and show it to the user for review. When a session yields several specs, draft them all here. Iterate in chat until they sign off. This is the last cheap place to change direction.

Frontmatter (per the template + contract):

```yaml
---
type: spec
id: spec-<slug>            # canonical id; matches the filename slug
status: proposed           # a finalized, ready-to-execute spec is `proposed` (not `accepted` — that's set when work ships; `draft` = not ready)
value: <deck>
difficulty: <deck>
owner: <optional>
tags: [<phase-N>, <area>, …]
related_docs: [ARCHITECTURE.md, PRD.md, …]
related_worktrees: []
related_sessions: []
depends_on: [spec-<prereq-slug>, …]   # omit or [] if none
created: <today>
updated: <today>
---
```

Body: the 9 required sections, in order, with a `# Spec: <Title>` heading. Acceptance criteria as **unchecked checkboxes** (`- [ ]`) — they're the contract Execute-mode ticks. Reference the related specs/ADRs inline where they constrain a decision.

### Step 6 — Land the spec(s) in one worktree

Once the user signs off on the content, write **every** spec from this session into a **single** worktree — never one worktree per spec (they're just markdown files; a worktree apiece is pure overhead). Execution splits one-spec-per-worktree later; planning does not.

**Two entry modes — check before creating a worktree (mirrors `execute-local-worktree`).** Run `git rev-parse --abbrev-ref HEAD` and `pwd`:

- **From `main` (the common case — you typed `/planner …` in a terminal).** Follow Step 6.1 below: `mise run wt:new` creates the planning worktree, then write the specs into it.
- **Already inside a daemon-spawned worktree (the Plan-button prompt-spawn, spec prompt-spawn-planner-from-ui).** If you are **NOT** on `main`/`master` **and** your `pwd` is under `.claude/worktrees/<…>`, the daemon already created this worktree for you and dropped you in it. **SKIP Step 6.1 `mise run wt:new`** — do not create or enter a second worktree (that would nest one). Run `mise run wt:setup` once if deps aren't installed, then continue at Step 6.3, writing the spec file(s) into **this** worktree.

1. `mise run wt:new <name>` — creates `.claude/worktrees/<name>/` on branch `worktree-<name>`, off HEAD, and bootstraps deps (`mise run setup`) so `pre-commit` is runnable. Name it for the work: `spec-<slug>` for a single spec, or a short theme slug covering the set (e.g. `spec-<theme>`) when the session produced several. **(From-`main` entry only — skip when already inside a daemon-spawned worktree.)**
2. Enter the worktree (the `EnterWorktree` tool, or `cd` into it for git ops). **(From-`main` entry only — you are already inside it in the daemon-spawned case.)**
3. **Create** each agreed spec with `mise run docs:new spec --json '{"name": "<title>", "value": N, "difficulty": N, "tags": [...]}'` *inside the worktree*, one per spec — it scaffolds the unique filename + id + section skeleton; then `Edit` the file to fill the sections. (A direct `Write` of a new doc is blocked by the hook → use `docs:new`; editing the scaffold is allowed.)
4. Commit them — **one `docs(spec):` commit per spec** for clean history: `git add docs/specs/<file> && git commit -m "docs(spec): add <short title>"`, repeated for each spec. `pre-commit` runs on every commit.
5. **Green `pre-commit`.** A markdown-only spec still goes through doc/markdown linters and spelling checks — unknown technical terms may need adding to the dictionary, or rephrasing. Iterate until `mise run pre-commit` is clean. Never `--no-verify`.
6. *(Optional)* If `main` moved, `mise run wt:rebase` so the user's later fast-forward is clean. For brand-new files this rarely conflicts.

### Step 7 — Hand off (stop here)

Report and **stop** — do not land the spec(s), do not start implementing them:

```
Spec(s) drafted & committed — ready for you to land

Worktree:  <name>
Branch:    worktree-<name>
Specs:     docs/specs/<YYYY-MM-DD-HHMMSS>-<slug>.md   (status: proposed)
           …one line per spec when the session produced several…
Commits:   <N> docs(spec): commit(s) — one per spec
Pre-commit: ✅  (pre-stop: pending)
Related:   <overlapping specs / constraining ADRs / depends_on prereqs you surfaced>
WSJF:      <slug>  value <v> / difficulty <d>   (one line per spec)

Next:
  • Land them (your op):   mise run wt:land <name>
  • Then execute each:     /execute-local-worktree on docs/specs/<…>.md   (one execution worktree per spec)
```

## Critical rules

- **PLANNING ONLY.** Produce the spec(s) and stop. Never implement the spec's work, never tick its acceptance criteria, never set `status: accepted`. That's `/execute-local-worktree`.
- **EXTRACT, then write.** Don't write the spec from a thin prompt — run the brainstorming loop (Steps 2–5) and get the user's sign-off on the full content before any file is created.
- **ACCEPTANCE CRITERIA ARE THE CONTRACT.** Testable end-states, never implementation steps. This is where the conversation pays off — make them sharp.
- **DETECT DUPLICATES & RELATED WORK before finalizing** (Step 3). Surface overlaps and let the user decide new-vs-extend-vs-supersede. Never silently create a near-duplicate.
- **ONE WORKTREE PER SESSION.** Default-deny blocks edits to the main checkout — `mise run wt:new` first, then write **every** spec from this session under that **single** `.claude/worktrees/<name>/`. Never one worktree per spec; execution splits per spec, planning does not.
- **DECK-ONLY estimates** (`0.5,1,2,3,5,8,13`); `difficulty > 13` ⇒ split the spec. Never write a `priority:` field.
- **`status: proposed`** for a finalized, ready-to-execute spec — not `accepted` (set when work ships), not `draft` (not ready).
- **GREEN `pre-commit`** on the spec commit, including the spelling/doc linters. Never `--no-verify`.
- **HAND OFF, don't land.** `mise run wt:land` is the human's op. Stop at the hand-off summary.
- **NO attribution trailers** in the commit — no `Co-Authored-By`, no "Generated with Claude Code".
