# DOCS.md — Documentation Conventions

> Seeded by `agenticd init`. This file describes how docs are structured in this repo.
> Edit it freely — the conventions below are the baseline the harness expects, not a
> read-only contract.

## 1. The purpose of structured docs

The harness keeps three kinds of durable knowledge in `docs/`:

1. **Decisions** — *why* the codebase is shaped the way it is (ADRs).
2. **Specs** — *what* an agent is asked to build, with testable acceptance criteria.
3. **Memory** — *what an agent learned* while executing a spec, so the next agent
   doesn't rediscover the same lesson.

Everything else (reference notes, vendor docs) lives alongside these. All
docs use the same frontmatter schema so the harness can index, link, and surface them
without parsing prose.

---

## 2. Folder layout

```
docs/
  decisions/    ADRs — hard-to-reverse or cross-cutting choices
  specs/        Work specs — one per worktree loop, one agent, one task
  memory/       Retrospectives — what was learned in a completed sprint
  knowledge/    Reference notes, vendor-doc snapshots, how-to guides
```

The tree is nested by **purpose**, not by date, team, or author. Dates live in the
filename prefix (see §3) and in frontmatter fields, so the folder stays topically
browsable.

---

## 3. Filename convention

Files under `docs/specs/` and `docs/decisions/` use a clock-derived prefix:

```
YYYY-MM-DD-HHMMSS-slug.md
```

Examples:
```
docs/specs/2026-06-04-140000-user-auth-flow.md
docs/decisions/2026-06-04-140012-choose-sqlite-over-postgres.md
```

**Why clock-derived, not sequential?** Sequential numbers require a global counter that
breaks under parallel agents or concurrent branches. A wall-clock timestamp is
collision-free in practice (two agents rarely create a doc in the same second) and
append-only — no reordering, no renumbering, no merge conflicts on an index file.

Use seconds precision. Local time is fine; UTC is fine. Be consistent within a repo.

`docs/memory/` and `docs/knowledge/` use a shorter date prefix or a plain slug —
their order matters less:

```
docs/memory/2026-06-05-user-auth-flow.md
docs/knowledge/sqlite-fts5.md
```

**Don't hand-write the filename — use the creation tool.** Create every doc node with:

```
mise run docs:new <type> --json '{"name": "user auth flow", "tags": ["auth"], "body": "…"}'
```

where `<type>` is `spec` · `decision` · `memory` · `knowledge`. It stamps the
collision-free `YYYY-MM-DD-HHMMSS-slug.md` name and a contract-correct frontmatter block,
then prints the path so you can `Edit` the body. **A direct `Write` of a new file under
`docs/{specs,decisions,memory,knowledge}/` is blocked by the policy hook** — `docs:new` is
the only sanctioned creation path (`docs:list` / `docs:show` / `docs:search` query existing
nodes).

---

## 4. Frontmatter contract

Every doc under `docs/` must open with YAML frontmatter. The harness indexes on
these fields; missing or malformed frontmatter causes the doc to be skipped.

```yaml
---
type: spec          # spec | decision | memory | knowledge | general
id: spec-user-auth-flow            # <type>-<slug>, no spaces
status: proposed    # proposed | accepted | superseded | archived | draft
tags: [auth, phase-1]
related_docs:
  - decision-choose-sqlite-over-postgres
  - spec-session-management
created: 2026-06-04
updated: 2026-06-04
---
```

**`status` lifecycle:**
- `proposed` — written, not yet implemented.
- `accepted` — spec is done; all acceptance criteria ticked. Decision is in force.
- `superseded` — replaced by a newer doc (add a `superseded_by:` field pointing at it).
- `archived` — no longer relevant; kept for history.
- `draft` — placeholder; not ready for an agent to pick up.

**`related_docs`** — list the `id` values of docs this one depends on or refers to.
The harness uses these links to build a dependency graph and surface context to agents.
Use the `id` slug, not a file path, so links survive renames.

---

## 5. ADR conventions (`docs/decisions/`)

Write an ADR for any decision that is:
- Hard to reverse once the codebase depends on it.
- Cross-cutting (affects multiple crates, modules, or workflows).
- Likely to surprise a future contributor who doesn't know the backstory.

**Do not** write an ADR for: routine dependency bumps, implementation details, or
decisions that are obvious from the code.

### Required sections

| Section | What it contains |
|---|---|
| **Context** | The situation that forced a decision. What constraints existed? |
| **Decision** | The choice made, stated plainly. |
| **Alternatives** | What else was considered and why it was rejected. |
| **Consequences** | What changes as a result — good and bad. |
| **Migration** | How to move existing code/data to the new approach (if applicable). |
| **Follow-ups** | Open questions or deferred work that this decision surfaces. |

### The append-only rule

When a decision changes, write a **new** ADR that supersedes the old one. Set
`status: superseded` and `superseded_by: decision-<new-slug>` on the old doc.
Never edit a past decision's **Decision** section — the history of *why* choices
were made is the whole point.

### Example skeleton

```markdown
---
type: decision
id: decision-choose-sqlite-over-postgres
status: accepted
tags: [persistence, phase-1]
related_docs: []
created: 2026-06-04
updated: 2026-06-04
---

# Decision: use SQLite as the queryable index

## Context
The tool runs locally for a single user. An embedded database avoids a
separate server process and simplifies distribution.

## Decision
Use SQLite (via SeaORM) as the queryable index for all structured data.

## Alternatives
- **PostgreSQL** — overkill for single-user local use; requires a running server.
- **DuckDB** — good for analytics but less mature ORM support.

## Consequences
- No network-accessible database — acceptable for local-first scope.
- Migrations are required from day one; schema drift breaks the index.

## Migration
N/A — greenfield.

## Follow-ups
- Decide on WAL mode for concurrent reads.
```

---

## 6. Spec conventions (`docs/specs/`)

A spec is the **contract** between a human (or planner agent) and an executing agent.
One spec = one worktree = one agent run. Never bundle two unrelated specs into one
worktree; never start a worktree without a spec.

The executing agent reads the spec before writing a line of code. Acceptance criteria
are the definition of done.

### Required sections

| Section | What it contains |
|---|---|
| **Problem** | Why this work exists. What breaks or is missing without it? |
| **Goals** | What success looks like — outcomes, not tasks. |
| **Non-goals** | What is explicitly out of scope. Prevents scope creep. |
| **User workflow** | A short narrative of how a user experiences the change. |
| **Requirements** | Labelled requirements (R1, R2, …) the implementation must satisfy. |
| **Acceptance criteria** | Testable `- [ ]` checkboxes. Each maps to a verifiable end-state. |
| **Test strategy** | How the criteria will be verified (unit, integration, e2e, manual). |
| **Risks** | What could go wrong and the planned mitigation. |
| **Open questions** | Unresolved ambiguities that block or affect the implementation. |

### Acceptance criteria discipline

Criteria describe **what must be true when the work is done**, not how to get there:

```markdown
# Good — testable end-state
- [ ] After `init_repo`, `.claude/hooks/pre_tool_use.py` exists and is byte-identical
      to the bundle.
- [ ] A second `init_repo` on the same path reports all files `unchanged` and writes
      nothing to disk.

# Bad — implementation instructions
- [ ] Write the hook file to .claude/hooks/.
- [ ] Add an idempotency check.
```

Checkboxes become `[x]` only when the criterion is demonstrably satisfied — not when
the code "looks right". If a criterion cannot be ticked, the spec is not `accepted`.

### The spec lifecycle

```
proposed → (agent picks it up) → accepted
         → (work obsoletes it) → superseded
         → (no longer needed)  → archived
```

The agent who closes the spec:
1. Ticks all satisfied `- [x]` criteria.
2. Sets `status: accepted` and bumps `updated:`.
3. Adds an `## Outcome` section summarising what landed.
4. Writes a retrospective in `docs/memory/` (see §7).

### Example skeleton

```markdown
---
type: spec
id: spec-user-auth-flow
status: proposed
tags: [auth, phase-1]
related_docs: [decision-choose-sqlite-over-postgres]
created: 2026-06-04
updated: 2026-06-04
---

# Spec: user authentication flow

## Problem
Users have no way to log in. All routes are unprotected.

## Goals
1. Users can sign in with email + password.
2. Sessions persist across browser refreshes.

## Non-goals
- OAuth / social login (follow-up spec).
- Password reset flow (follow-up spec).

## User workflow
A visitor navigates to /login, enters credentials, and lands on the dashboard.
Subsequent visits skip the login page while the session is valid.

## Requirements
- **R1** — POST /auth/login returns a session token on valid credentials.
- **R2** — The session token is stored in an httpOnly cookie.
- **R3** — GET /api/me returns the current user from a valid session.

## Acceptance criteria
- [ ] POST /auth/login with valid credentials returns 200 and sets a cookie.
- [ ] POST /auth/login with invalid credentials returns 401.
- [ ] GET /api/me with a valid session returns the user object.
- [ ] GET /api/me with no session returns 401.
- [ ] Session survives a page refresh (cookie persists).

## Test strategy
Integration tests for the API endpoints. E2e spec for the login → dashboard flow.

## Risks
- Cookie SameSite settings may conflict with the dev proxy. Mitigation: test in
  the actual dev server, not just unit tests.

## Open questions
- Session TTL: 7 days rolling or fixed? Default to 7 days rolling.
```

---

## 7. Memory conventions (`docs/memory/`)

A memory doc is a **retrospective** written at the end of a worktree sprint. It
captures what the executing agent learned during that sprint. It is **not** a summary
of what was built — that belongs in the spec's `## Outcome` section. The memory doc
is the feedback loop that prevents history from repeating.

The retrospective serves **two equal purposes**:

1. **Application/code feedback** — bugs found, architectural flaws, wrong patterns or
   abstractions, library surprises, test gaps. Every problem that surfaced should become
   unrepeatable: a regression test, a lint rule, a gate, an enforced convention.

2. **Agentic process feedback** — tooling friction, missing context, slow or flaky gates,
   steps that could be automated, anything that made the agent's loop slower or more
   error-prone than it should be.

Both channels feed the same invariant: **any problem only happens once.** A bug that is
caught gets a regression test. A pattern violated because it wasn't documented gets added
to CLAUDE.md or enforced by a gate. Tooling that fought the agent gets a script or a cached
artifact. The memory file turns a one-time observation into a permanent improvement.

### The workflow that produces a memory

```
planning (planner agent writes a spec)
  → execution (agent runs /execute-local-worktree on the spec)
    → sprint inside the worktree (implement, commit, rebase, self-review)
      → hand-off (spec accepted, gate green, branch rebased)
        → retrospective → memory doc
```

The memory doc is written **inside the worktree** before hand-off, as part of the
loop close. It is committed with the implementation so the human reviews both together.

### When to write one

Write a memory when the run surfaced **at least one** of:
- A bug, crash, or test failure whose root cause was a missing convention or pattern.
- An architectural flaw, wrong abstraction, or mismatched library that caused real pain.
- A pattern the agent violated because it wasn't documented anywhere.
- A gate, test, or check that was flaky, slow, or gave misleading feedback.
- A step that took significant time and could be automated or cached.
- Context that was missing at the start and had to be discovered by trial and error.

Do **not** write a memory for a routine, no-surprise run. "Nothing went wrong" is
not worth recording. If you cannot name the finding in one sentence, skip it.

---

### Channel 1: Application and code feedback

This channel answers: *what did we learn about the application — its bugs, patterns,
architecture, and tests — that should prevent future problems?*

**Bugs and regressions**
Document the root cause, not just the symptom. Propose the regression test and
any enforcement (lint rule, gate check) that makes this category of bug unrepeatable.
_Example: "Null deref in the session handler when `user_id` is absent from the token.
Added `ensure!` guard. Regression test: `test_session_missing_user_id`."_

**Architectural flaws and wrong abstractions**
If the implementation revealed that the chosen abstraction, library, or approach
doesn't fit — document the mismatch and what a better design looks like. Input for
a future ADR or refactor spec.
_Example: "`Arc<Mutex<EventBus>>` causes lock contention. The design assumes
single-producer; `tokio::sync::broadcast` removes the mutex and fits the actual
usage pattern."_

**Pattern violations and missing conventions**
If the agent used an anti-pattern because the right pattern wasn't documented —
and found out only later via a gate failure, reviewer comment, or runtime crash —
that is a documentation and enforcement gap. Name the pattern and propose enforcement.
_Example: "Used `unwrap()` in library code; clippy caught it after 20 commits.
Convention (no `unwrap()` in lib crates) should be in CLAUDE.md and enforced by
`clippy::unwrap_used`."_

**Test gaps**
Anything that broke because there was no test for it. Name the failure and propose
the specific test class that would have caught it.

---

### Channel 2: Agentic process feedback

This channel answers: *what made the loop slower, harder, or less certain — and how
do we fix that for the next agent?*

**Was context sufficient at the start?**
Did the agent have everything it needed — spec clarity, conventions, task reference —
or did it have to discover missing context mid-sprint? Name any gaps and what document
or seeded default should fill them.

**Goal and spec clarity**
Were acceptance criteria unambiguous? Were requirements underspecified? These feed
back to the planner or become spec-writing guidelines.

**Where did the sprint spend the most time?**
Name the bottleneck with timing. Timing estimates drive prioritization — a 10-minute
bottleneck caught early saves every future sprint.
_Example: "Unit suite: 15s × 40 commits = ~10 minutes. `test:unit:changed` using
`cargo nextest --changed-files` would cut this to under 5 runs."_

**What work was redundant or could be automated?**
Steps done manually that a script, cached artifact, or seeded default could handle.
Propose the specific task.

**Tooling friction**
Gates that were slow, flaky, or gave misleading error messages. Commands blocked by
default-deny that shouldn't have been (or vice versa). Each friction point is a
concrete improvement target.

**Scripts or automation proposals**
Concrete `mise run` tasks or harness changes that would make the next sprint faster
and less error-prone.

---

### What to put in it — example

Label each finding `[Code]` or `[Process]` so the two channels are scannable.

```markdown
---
type: memory
id: memory-user-auth-flow
status: active
tags: [auth, bugs, process]
related_docs: [spec-user-auth-flow]
created: 2026-06-05
updated: 2026-06-05
---

# Retrospective: user-auth-flow sprint

## [Code] Cookie SameSite — violated pattern, no enforcement

Used `SameSite=Strict` (Axum default). Only after the e2e failed and ~45 min of
debugging did I find that OAuth redirects strip strict cookies. The `SameSite=Lax`
requirement wasn't in CLAUDE.md or any auth doc.

**Regression:** `test_oauth_redirect_preserves_session` added to `auth_tests.rs`.
**Enforcement:** Propose grep gate: warn on `SameSite=Strict` in session config.
Add to CLAUDE.md: "all session cookies must be `SameSite=Lax`."

## [Code] Missing guard on `user_id` — null path untested

Session handler panicked when `user_id` was absent from the token payload.
No test existed for the missing-claim path.

**Fix:** Added `ensure!(claims.user_id.is_some(), ...)`. Regression test added.
**Pattern:** Any `Option` extracted from external input must have a test for the
`None` case. Propose adding to CLAUDE.md under "error handling conventions."

## [Process] Unit tests ran ~40× at 15s each — 10 min bottleneck

Every commit triggers the full unit suite. With ~40 commits, ~10 min was gate wait.

**Proposal:** `mise run test:unit:changed` using `cargo nextest --changed-files`.
Single highest-ROI pipeline change for this stack.

## [Process] `cargo fmt --check` failed on untracked files — caught late

Formatted only staged files; the gate checks ALL `.rs` files including untracked.
Wasted 3 re-commit cycles before running `mise run format` on the whole tree.

**Fix:** `wt:new` now calls `wt:setup` which includes a full-tree format. Fixed
upstream. Add a note to the gate failure message pointing to `mise run format`.
```

### What NOT to put in it

- A summary of what was built (→ goes in the spec's `## Outcome`).
- Implementation details visible from the code.
- Anything already covered in CLAUDE.md or this file.
- Vague findings with no proposed action ("tests could be faster" with no timing
  and no proposed fix is not worth recording).

---

## 8. Knowledge conventions (`docs/knowledge/`)

Reference notes and vendor-doc snapshots. Use these when you pull external
documentation during a sprint and want to keep the authoritative version for
future sessions.

Required extra frontmatter fields:

```yaml
source_url: https://docs.example.com/api/reference
source_pulled_at: 2026-06-04
```

This makes staleness visible. A knowledge doc without `source_pulled_at` may be
stale without warning. Re-pull via `mise run docs:sync` or manually when the
library releases a breaking change.

---

## 9. Cross-referencing docs

Use the `id` slug in `related_docs` frontmatter, not a file path:

```yaml
related_docs:
  - spec-user-auth-flow        # not docs/specs/2026-06-04-140000-user-auth-flow.md
  - decision-choose-sqlite
```

In prose, use double-bracket notation: `[[spec-user-auth-flow]]`. The harness
resolves these to the file when indexing.

File paths rot as docs are renamed; `id` slugs are stable.
