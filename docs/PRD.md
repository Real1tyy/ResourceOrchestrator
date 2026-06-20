# Resource Orchestrator — Product Requirements Document

> **Status:** v1.0 — Finalized requirements (brainstorm fully captured) · **Owner:** Real1ty · **Date:** 2026-06-20
> **Scope of this doc:** *What* and *why* (product requirements + feasibility + build-vs-buy).
> Architecture, tech stack, and detailed API/schema design are tracked separately.

---

## 1. One-line pitch

> **Working name / CLI aliases:** `roch` · `resource-orchestrator`

A local, resource-aware **task-orchestrating proxy** for agentic engineering: AI agents
(and humans) submit their build/lint/test/pre-commit pipelines to it instead of running
them directly. It **governs system resources** (so 10 parallel agents don't thrash the
machine), **monitors and streams** everything in real time, and **records full history +
statistics** so slow loops can be measured and optimized.

Think: **"Docker + systemd + a job queue + Grafana, fused into one daemon, purpose-built
for the case where N coding agents share one workstation."**

**Value proposition — three reinforcing pillars of value:**

1. **Efficient resource utilization.** A shared budget + queue keeps N parallel agents from
   thrashing the box, so heavy work runs reliably instead of oversaturating and flaking.
2. **Monitoring & observability.** A single live view of *what the agents are actually doing* —
   running/queued tasks, per-task resource use, streamed logs, system-wide saturation.
3. **History & statistics.** Every run is recorded and aggregated, turning "pre-commit feels
   slow" into measurable p50/p95-per-repo numbers you can optimize and re-measure against.

---

## 2. Problem statement

When orchestrating multiple AI coding agents on a single machine, each agent independently
runs heavy, bursty workloads — `cargo test`, `cargo clippy`, `playwright`, `vitest`,
full pre-commit pipelines. There is no shared awareness of the machine's finite resources.

Concrete failure modes observed today:

- **Resource oversaturation → flaky failures.** 5 agents each launch the integration suite;
  the machine has 32 cores but now ~160 logical test workers fight for them. Tests miss
  **time-based assertions and timeouts**, fail spuriously, and the agents waste cycles
  "debugging" failures that are purely contention artifacts.
- **Memory pressure → system-wide stall.** Parallel test/build runs spike RAM; the box hits
  swap, thrashes, and *everything* — including the IDE and the agents themselves — lags.
  (External research confirms **memory, not CPU, is the dominant concurrency bottleneck**
  for agent workloads, with peak-to-average memory spikes of ~15× — see §13.)
- **Zero observability.** No single place shows what the agents are *actually running*,
  for how long, from which repo, consuming what. Logs are scattered across agent transcripts.
- **No history / no optimization loop.** "Pre-commit feels slow" is unmeasurable. There's no
  median/p95 runtime per task per repo, no way to see which step in a pipeline is the tight loop.
- **Redundant work.** Two agents in two worktrees run the identical test suite against the
  identical code state, doubling load for one result.
- **Fixed-resource / port collisions.** Beyond CPU/memory/disk I/O, parallel agents also
  contend for *fixed* resources — TCP ports (two dev servers or test harnesses binding the same
  port), a shared scratch DB, or a single GPU — where the conflict is binary, not gradual. These
  cause hard failures ("address already in use", DB lock) that look like code bugs but are
  contention artifacts. (Modeled via exclusive concurrency groups — see C5/C6.)

**Resource Orchestrator** turns "every agent for itself" into a governed, observable,
deduplicated shared service.

---

## 3. Goals & non-goals

### 3.1 Goals (what success looks like)

1. **Resource governance.** Tasks run inside enforced CPU/memory limits drawn from a global,
   user-configured budget. The orchestrator guarantees the *sum* of admitted tasks stays
   within budget; excess work queues.
2. **Monitoring & observability.** A live view of running / queued tasks with per-task CPU,
   memory, runtime, origin repo, agent, and tags — plus real-time streamed logs per task.
3. **History & statistics.** Every run persisted with state, exit code, timing, logs, origin.
   Rich filtering, aggregation, and per-task percentile/median analysis to find slow loops.
4. **System/node monitoring + guardrails.** A node-exporter-style view of the whole machine
   (CPU, RAM, disk, swap) with configurable threshold notifications and automatic
   back-pressure / preemption when the system (including *external* load) approaches danger.
5. **Agent-friendly interface.** Agents submit a task, **block, and sleep** until it finishes;
   minimal client overhead; tolerant of multi-minute long-running pipelines.

### 3.2 Non-goals (explicitly out of scope, at least for MVP)

- **Cross-machine / distributed scheduling.** Single Ubuntu workstation only. No cluster.
- **Cross-OS support.** **Ubuntu/Linux only.** No macOS/Windows. We will freely depend on
  cgroups v2, systemd, and `/proc`.
- **Replacing the agents' decision-making.** We orchestrate *task execution*, not agent logic.
- **Being a CI system.** No remote runners, no pipeline-as-code DSL of our own (we *proxy*
  existing runners like mise; we don't replace them).
- **Security sandboxing against malicious code.** Isolation here is for *resource* safety and
  organization, not a hostile-multi-tenant security boundary (the agents are trusted).
- **Replacing mise (or systemd for all services).** We *wrap and proxy* mise; daemon management
  (Pillar K) augments systemd, it doesn't replace it system-wide.
- **Auto-killing arbitrary external processes.** External load is *detected and surfaced*, never
  auto-killed (see principle P2, §3.4). We only ever pause/kill tasks **we** own.
- **Deferred / explicitly out for now:** GPU scheduling; eBPF-based process tracing; shared/remote
  cache; team mode, web login/auth, cloud-hosted SaaS dashboard; plugin marketplace; deep semantic
  test selection or automatic dependency-graph inference; perfect cache correctness for arbitrary
  shell commands.

### 3.3 Operating constraints

- Single user, single machine, trusted callers. Optimize for *self* first; open-source later.
- Linux/Ubuntu, cgroups v2 unified hierarchy, systemd present.

### 3.4 Product principles

- **P1 — Git & task metadata are truth.** Record repo path, worktree, branch, commit SHA, dirty
  state, and task metadata whenever available; every run is anchored to *where it came from*.
- **P2 — Controlled tasks are safer than uncontrolled ones.** Auto-pause/kill applies **only to
  tasks the orchestrator owns**. External load is surfaced, never auto-killed by default.
- **P3 — Scheduling beats brute force.** Prefer queueing, fairness, budgets, and back-pressure
  over letting every agent run immediately.
- **P4 — History enables optimization.** This is not just a runner; it's a **feedback-loop
  profiler** that reveals which tasks waste the most developer and agent time.
- **P5 — Start simple, add correctness later.** MVP solves queueing, governance, execution, logs,
  and history first. Perfect caching, sandboxing, and task-graph inference come later.

---

## 4. Users & primary use cases

**Primary user:** a power developer orchestrating multiple coding agents locally on Ubuntu —
across **Rust, TypeScript, Python, and (future) Go** — using isolated git worktrees, frequent
harness checks (format / lint / build / test / pre-commit / pre-stop / verification loops), and
**mise** as the task runner. Wants aggressive automation *without* letting agents overload the box.
**Primary "clients":** AI agents (Claude Code / Codex / terminal sessions, via hooks/SDK) and the
developer via CLI/UI.

**Secondary users:** open-source devs running multi-agent local workflows; teams with expensive
monorepo verification; anyone wanting a local **"CI-lite" dashboard** (history, runtime, logs,
resource usage) before pushing to real CI.

Representative use cases:

- **UC-1 — Proxied pre-commit.** Agent finishes a change and would run `mise run pre-commit`.
  Instead it submits the task to the orchestrator, which admits it when resources allow,
  runs it under limits, streams logs, and returns the exit code. Agent blocks until done.
- **UC-2 — Contention avoidance.** 10 agents submit test suites simultaneously. Orchestrator
  admits e.g. 4, queues 6, distributes cores, and the suites pass reliably instead of flaking.
- **UC-3 — Live monitoring.** Developer opens the UI, sees 4 running + 6 queued tasks, clicks
  one, watches its console stream live, sees it's pinned to 4 cores using 3.2 of them and 1.8 GB.
- **UC-4 — Optimization loop.** Developer opens history, filters to `pre-commit` in repo X,
  sees median 4m12s / p95 9m, drills into one run, sees the pie chart says `test` is 80% of
  wall-clock. Simplifies the suite, re-measures next day, confirms improvement.
- **UC-5 — System guardrail.** An external process (not ours) eats 20 cores and RAM hits swap.
  Orchestrator detects the pressure, pauses/kills the least-important tasks to let the system
  breathe, then resumes/restarts them when pressure clears.
- **UC-6 — Dedup (later).** Two worktrees submit the same suite with the same input fingerprint;
  the second is joined to the first (or served from cache) instead of re-run.
- **UC-7 — Daemon management (later).** A long-running dev server is registered as a managed
  daemon; the orchestrator starts/stops it, caps its resources, and streams its logs in the UI.

Concrete submission shape (illustrative CLI; transport TBD in architecture phase):

```bash
roch run \
  --repo  /home/real1ty/Programming/agentic-workflow \
  --agent claude-session-auth-01 \
  --task  pre-commit \
  --tag   rust \
  -- mise run pre-commit
# blocks, streams logs, exits with the task's exit code
```

---

## 5. Core concepts & terminology

| Term | Definition |
|---|---|
| **Task (definition)** | A named unit of work, e.g. `pre-commit`, `test`, `lint`. May decompose into **steps/subtasks**. |
| **Run** | A single concrete invocation/instance of a task. The thing that gets queued, executed, recorded. Has a unique id. |
| **Step / Subtask** | A child unit inside a run (e.g. `pre-commit` → `format`, `lint`, `test`). Recursively nestable. Individually timed/logged/resourced (later phase). |
| **Daemon** | A long-running, non-terminating managed process (dev server, watcher). Monitored & resource-capped but never "succeeds". Separate category. (Later phase.) |
| **Origin** | Where a run came from: repository path + worktree + branch + commit SHA + dirty state (whatever git exposes), plus working directory. |
| **Caller / Agent identity** | Who submitted it (e.g. `claude-session-auth-01`, `human`). Free-form, supplied at submit time. |
| **Tag** | Optional user labels on a run (e.g. `rust`, `frontend`, `e2e`). For filtering/grouping. |
| **Resource Pool / Budget** | The global ceiling of CPU (cores) and memory the orchestrator may hand out to all tasks combined. |
| **Slot** | A concurrency unit. Global max-concurrent-tasks caps the number of simultaneously *running* runs. |
| **Concurrency group** | A *named* limit shared across runs (e.g. task-type `playwright` max 1; `integration-test` max 1 **per repo**; `db` max 1 **globally**). A group of capacity 1 is an **exclusive lock / mutex**. |
| **Policy** | Rules deciding whether a run is allowed, queued, limited, denied, or requires approval. |
| **I/O & scheduling priority** | Optional per-run `nice` (CPU scheduling) and `ionice` (disk I/O) niceness, layered on top of cgroup limits. |
| **Profile / Preset / Resource Pool** | A named, swappable bundle of config (budget + concurrency + thresholds), e.g. `interactive`, `balanced`, `heavy`, `overnight`. |
| **Fingerprint / Cache key** | A deterministic hash of a run's relevant inputs, used for caching & dedup. (Later phase.) |
| **Admission** | The decision to move a run from `queued` to `running` based on available budget/slots. |
| **Preemption** | Pausing/killing an admitted run to relieve system pressure. |

---

## 6. Functional requirements

Organized into pillars. Each requirement tagged with a target phase
(**MVP**, **P2**, **P3**) — see §10 for the roadmap.

### Pillar A — Task proxying & execution gateway

- **A1 (MVP).** Accept a run submission containing: command (or task name + args), origin
  (repo path / worktree), caller identity, optional tags, optional resource overrides,
  environment/cwd. Return a run id immediately.
- **A2 (MVP).** Execute the submitted command as a child process under enforced resource
  limits (Pillar B), capturing stdout/stderr (merged + separable), exit code, and timing.
- **A3 (MVP).** Two submission modes:
  - **Blocking** — connection stays open; response returns when the run terminates (exit code +
    summary). This is the agent's default ("submit, block, sleep").
  - **Async** — returns the run id immediately; caller polls or subscribes for completion.
- **A4 (MVP).** Long-run safe: connections survive multi-minute (target: ≥30 min) runs without
  idle disconnects (keepalives/heartbeats). A dropped client connection does **not** kill the run.
- **A5 (MVP).** Cancellation: a run can be cancelled by id (graceful SIGTERM → grace period →
  SIGKILL of the whole process group/cgroup).
- **A6 (P2).** **mise integration.** Submit `mise run <task>` and have the orchestrator
  understand the task graph: enumerate steps/deps (via `mise tasks deps` / `--json`) and
  optionally run/track them as nested steps. (See §9 and §13.5.)
- **A7 (P2).** Manual step decomposition: caller may declare a run as composed of N named steps
  with their own commands, to be run (in parallel or sequence) as tracked subtasks.

### Pillar B — Resource governance & sandboxing

- **B1 (MVP).** A global **budget**: configurable max cores and max memory the orchestrator
  may allocate across all running tasks (e.g. "16 of 32 cores, 20 GB of 64 GB").
- **B2 (MVP).** A global **max-concurrency** (max simultaneously running runs, e.g. 5).
- **B3 (MVP).** **Distribution policy.** Given budget + concurrency, allocate per-task shares.
  Example: 16 cores / 5 tasks → one task gets 4, four get 3 (sum ≤ budget). Remaining
  submissions queue. (Allocation strategy — hard pinning vs. weighted fair-share — see §13.2;
  decision pending.)
- **B4 (MVP).** **Enforced limits per run** via cgroups v2 (systemd scopes/slices):
  CPU (quota and/or cpuset pinning and/or weight) and memory (high+max). A run physically
  cannot exceed its allocation. ("Docker, but for tasks.")
- **B5 (MVP).** **Per-task overrides.** A submission may request specific limits
  (e.g. "2 cores, 2 GB"), bounded by the global budget.
- **B6 (P2).** **Memory-aware admission.** Admission considers projected memory (from history,
  see §13.1) not just core count, because memory is the real bottleneck.
- **B7 (P3).** **Allow/deny authorization.** A configurable allow **and** deny list of tasks
  (by name, command **pattern**, or origin); explicit deny patterns supported. Unauthorized
  submissions are rejected (e.g. `403 Forbidden`), do not execute, and are recorded as `rejected`.
  (User framed it as "401/forbidden"; semantically **403**.)
- **B8 (MVP).** **I/O & process priority.** Optional per-run `nice` and `ionice` to keep the
  desktop responsive, layered on top of the cgroup CPU/memory limits.

### Pillar C — Scheduling & queueing

- **C1 (MVP).** Internal **queue** of pending runs. Admission when budget + a free slot allow.
- **C2 (MVP).** Queue visibility: position, count, and why each item is waiting (no slot / no budget).
- **C3 (MVP).** Basic ordering: FIFO with at least one tie-breaker (e.g. priority field). Priority
  levels (P2+).
- **C4 (P2).** **Dedup / join.** If an identical fingerprint (Pillar I) is already running,
  attach the new submission to the in-flight run; on completion, stream the result to all joiners.
- **C5 (MVP).** **Per-task-type / per-group concurrency caps.** Named concurrency groups with
  independent limits — e.g. global max 5, but `playwright` max 1, `rust-build` max 2,
  `integration-test` max 1 **per repository**, `db`-dependent suites max 1 **globally**.
  Concurrency groups also model **fixed/binary resources** (a TCP port, a scratch DB, a single
  GPU): bind the contending runs to a capacity-1 group so they serialize instead of colliding.
- **C6 (MVP).** **Exclusive resources (mutex).** A concurrency group of capacity 1 is a named
  lock; contending runs **block in the queue** (the agent waits) rather than failing, and the UI
  explains they are waiting on an exclusive resource. This is the core fix for flaky parallel
  integration suites (UC-2 / US-5).

### Pillar D — Real-time monitoring & observability

- **D1 (MVP).** Live dashboard of all runs by state: **running**, **queued**, recently finished.
- **D2 (MVP).** Per running task: live CPU%, memory, elapsed time, origin repo, caller, tags,
  allocated limits.
- **D3 (MVP).** **Live log streaming.** Click a run → stream its console output in real time
  (WebSocket/SSE). Works for both running and replayed-from-history runs. Logs are **preserved
  after completion**, **searchable**, **filterable by stdout/stderr**, downloadable/copyable, and
  the stream stays stable across multi-minute runs.
- **D4 (MVP).** Filtering/grouping in the live view by **origin repo**, **task type/name**,
  **caller/agent**, **tag**, **state**.
- **D5 (MVP).** Aggregated repository list: every origin the orchestrator has seen, with counts.
- **D6 (P2).** **ETA / progress estimation** from historical runtime of the same task+origin.
- **D7 (P2).** Nested view: drill into a run's **steps** with per-step timing, logs, resources,
  and a **per-run pie chart** of where wall-clock went (e.g. format 1% / lint 9% / test 90%).

### Pillar E — History, statistics & analytics

- **E1 (MVP).** Persist every run to a database with: id, task name, command, origin (repo +
  worktree + optional SHA), caller, tags, state, exit code, queued/started/ended timestamps,
  durations (queue wait + execution), peak/avg CPU & memory, allocated limits, log reference.
- **E2 (MVP).** **State machine** (see §7) recorded with transitions/timestamps.
- **E3 (MVP).** History table with filtering by task name, state, exit code, origin, caller,
  tag, time range; and sorting.
- **E4 (P2).** **Per-task statistics:** count, success rate, min/median/mean/**p75/p90/p95/p99**/max
  runtime, **queue-wait** time, resource averages, and **runtime trend over time** — sliceable by
  origin/tag/agent/time window; includes "slowest today", "most-run", and "most resource-heavy".
- **E5 (P2).** **Visualizations:** timeline of runs over time, pie chart of time/resources by
  task, activity heatmap, success/failure breakdowns.
- **E6 (P2).** **Problematic-task detection:** surface tasks with high failure rates, growing
  runtime, or outlier resource use (heuristics over the stats + logs). Includes **flaky-task
  candidates** — tasks that intermittently pass/fail on the *same* input fingerprint, a strong
  signal of contention-induced rather than code-induced failure.
- **E7 (P2).** Log retention policy (size/age caps, compression) so history doesn't grow unbounded.

### Pillar F — System / node monitoring (node-exporter-like)

- **F1 (MVP).** **Hardware detection:** total cores, total RAM, disk capacity at startup.
- **F2 (MVP).** **Global system page:** live whole-machine CPU utilization (per-core + aggregate),
  RAM used/free, swap activity, disk usage/free, load average — *including non-orchestrator load*.
- **F3 (P2).** Per-process attribution: "what is using the most RAM / disk / CPU right now"
  (top offenders), and disk-usage suggestions ("largest directories") on low-space warnings.
- **F4 (MVP).** This page is independent of the task system and useful on its own.

### Pillar G — Guardrails, preemption & notifications

- **G1 (MVP).** **Threshold notifications.** Configurable thresholds on CPU saturation, RAM
  remaining, swap activity, and **disk free** (e.g. "notify if free disk < 20 GB"). Emit a
  notification when crossed. Each threshold supports a **"guard delay"** (sustained-breach
  window / debounce + hysteresis) so notifications fire on genuine sustained pressure, not on
  transient spikes, and don't flap repeatedly around the threshold.
- **G2 (MVP).** **Admission blocker.** When the system is at/over a safety threshold (even from
  external load), stop admitting new runs.
- **G3 (P2).** **Active preemption.** Under genuine pressure (esp. swap thrash / OOM risk),
  pause or kill the least-important running tasks to protect the *system*. Detect pressure
  periodically; **resume/restart** preempted tasks once pressure clears. Principle:
  **the system's health outranks any task.**
- **G4 (P2).** Notification channels: at minimum desktop notification + in-UI; pluggable later.
- **G5 (P2).** Low-disk and high-RAM notifications include *actionable suggestions*
  (top consumers / largest paths).

### Pillar H — Configuration, presets & control knobs

- **H1 (MVP).** **Config panel** to set: core budget, memory budget, max concurrency, and all
  Pillar-G thresholds — adjustable live ("knobs").
- **H2 (MVP).** **Presets/profiles:** named bundles of the above, switchable from UI **and** CLI,
  affecting both scheduling and resource limits. Built-in set:
  - `interactive` — fewer cores, low concurrency; protect desktop responsiveness.
  - `balanced` — moderate concurrency (default).
  - `heavy` — high concurrency / high CPU allocation.
  - `overnight` — maximum safe resource usage (away from keyboard).
- **H3 (P2).** Per-origin / per-task default policies (e.g. "`e2e` in repo X is capped at 2 cores").
- **H4 (MVP).** Config persists across restarts; sane auto-detected defaults on first run.

### Pillar I — Caching & deduplication *(later phase)*

- **I1 (P3).** **Fingerprinting.** Compute a deterministic cache key for a run from declared
  inputs. Supported input sources include a **caller-supplied content hash** (e.g. the latest
  git SHA touching the relevant files) and/or task command + args + env.
- **I2 (P3).** **Result cache.** On a fingerprint hit within a freshness window, return the
  cached result (exit code + logs) without re-running. Recorded as a `cache_hit` run.
- **I3 (P3).** **In-flight dedup** (ties to C4): a fingerprint already running is joined, not duplicated.
- **I4 (P3).** Cache is observable (hit rate, what was served from cache) and invalidatable.

> Caching is explicitly **experimental / later**. The fingerprint contract (what the caller
> must pass for a cache to be *safe*) is the hard part and will be iterated — see §13.6.

### Pillar J — Step decomposition & parallel subtasks *(later phase)*

- **J1 (P2/P3).** Run a task as a set of **tracked steps** (parallel or sequential), each with
  its own resource slice, logs, timing, and exit code — rolling up into the parent run's result.
- **J2 (P3).** Recursive nesting (steps of steps).
- **J3 (P2).** Per-run **breakdown UI** (timeline + pie) over steps (see D7).

### Pillar K — Daemon / long-running service management *(later phase)*

- **K1 (P3).** Register **daemons** (long-running services) to be managed: start/stop, **start
  on boot / autostart**, restart policy, resource caps, log streaming, uptime & resource
  monitoring — a **GUI wrapper around (or lightweight replacement for) systemd** for dev
  services. Separate category from tasks.
- **K2 (P3).** Daemons appear in monitoring with uptime instead of completion, and obey the
  same global budget/guardrails.

### Pillar L — Client / SDK & agent hook integration

- **L1 (MVP).** A thin **client** (CLI + library) that submits a run, blocks, streams/forwards
  output, and exits with the run's exit code — a drop-in wrapper so a hook calling
  `mise run pre-commit` instead calls the orchestrator transparently.
- **L2 (MVP).** Ready-made **Claude Code hook** integration that proxies the relevant
  agent commands (pre-commit/test/build hooks) through the client.
- **L3 (P2).** Idempotent/retry-safe submission (a retried submit with the same idempotency key
  joins/returns the original rather than double-enqueuing).

---

## 7. Run lifecycle / state machine

```
                 ┌─────────────┐
   submit ─────► │   QUEUED    │
                 └──────┬──────┘
        (allowlist deny)│  (admission: budget + slot free)
            ┌───────────┼───────────────┐
            ▼           ▼                ▼
       ┌─────────┐  ┌─────────┐    ┌───────────┐
       │REJECTED │  │ RUNNING │    │ CACHE_HIT │ (I2)
       └─────────┘  └────┬────┘    └───────────┘
                         │
        ┌────────────┬───┼────────────┬───────────────┐
        ▼            ▼   ▼            ▼               ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
   │SUCCEEDED│ │ FAILED  │ │ KILLED  │ │TIMED_OUT│ │CANCELLED │
   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────┘

   Preemption (G3):  RUNNING ⇄ PAUSED  (and PAUSED → KILLED if pressure persists)
   Dedup (C4/I3):    QUEUED → JOINED(→ mirrors leader's terminal state)
```

Terminal states for stats: `SUCCEEDED`, `FAILED`, `KILLED`, `TIMED_OUT`, `CANCELLED`,
`REJECTED`, `CACHE_HIT`, `JOINED`. Every transition is timestamped.

---

## 8. Data model (high level — not final schema)

- **Run** — id, task_name, command, args, working_dir, origin {repo, worktree, branch, sha,
  dirty}, caller, tags[], state, exit_code, priority, concurrency_group, requested_limits,
  granted_limits {cores, mem, nice, ionice}, policy_decision, fingerprint?, dedup_group?,
  parent_run_id?, timestamps {submitted, admitted, started, ended},
  derived {queue_wait, exec_duration}, metrics {cpu_peak, cpu_avg, mem_peak, mem_avg}, log_ref.
- **Step** — run_id (parent), name, command, state, exit_code, timings, metrics, log_ref, order/parallel-group.
- **Daemon** — id, name, command, restart_policy, caps, state, started_at, log_ref. *(P3)*
- **Origin / Repository** — derived dimension (path, display name), aggregated counts/stats.
- **Profile** — name, budget {cores, mem}, max_concurrency, thresholds {...}, active flag.
- **CacheEntry** — fingerprint, result {exit_code, log_ref}, created_at, source_run_id, freshness. *(P3)*
- **SystemSample** — timestamp, per-core CPU, mem, swap, disk, load (time-series; retention-bounded).
- **Notification/Event** — type, severity, payload, timestamp, ack state.

---

## 9. Interface surface (high level — architecture TBD)

This section is **intentionally shallow**; the transport choice is an architecture decision.
Requirements the interface must satisfy:

- **Submit** a run (blocking or async) — long-lived, heartbeat-kept, survives multi-minute runs;
  client drop ≠ run kill.
- **Stream** logs/metrics for a run (push: WebSocket or SSE).
- **Query** runs/history/stats with filters.
- **Read** live system metrics.
- **Mutate** config/presets; **cancel** runs.

Candidate transports (to decide in architecture phase): a local HTTP/REST + WebSocket server,
or a Unix-domain-socket RPC, fronted by the thin client (L1). Submission "block & sleep"
maps cleanly onto either a long-lived streaming HTTP response or a submit-then-subscribe model.
Conceptually the agent acts as a **publisher that submits work and waits** — a *message-broker /
publish-and-wait* mental model: it parks with near-zero resource use until the orchestrator wakes
it with the result, and a single completed run can **fan its result out to multiple waiting
subscribers** (the dedup/join case, C4). The canonical blocking sequence is: (1) agent submits →
(2) orchestrator accepts/rejects → (3) on accept, run may queue → (4) agent process blocks →
(5) orchestrator runs the task under limits → (6) logs stream/buffer → (7) orchestrator returns
exit code + stdout/stderr summary + result metadata → (8) agent unblocks and continues.

**mise compatibility (A6):** mise exposes machine-readable task metadata
(`mise tasks --json`, `mise tasks deps [--dot]`), so the orchestrator can either (a) opaquely
proxy `mise run <task>` as a single run, or (b) introspect the dependency graph to track steps.
MVP does (a); (b) unlocks Pillar J.

---

## 10. Phasing / roadmap

### MVP (Phase 1) — "Governed, observed proxy"
The smallest thing that stops the thrashing and gives visibility.
- A1–A5 (submit/exec/block/cancel), B1–B5 + B8 (budget + cgroup limits + distribution + nice/ionice),
  C1–C3 + C5–C6 (queue + per-type/exclusive concurrency), D1–D5 (live monitoring + log stream +
  filters), E1–E3 (persist + history table), F1–F2/F4 (node monitor), G1–G2 (threshold notify +
  admission blocker), H1–H2/H4 (config + presets), L1–L2 (client + Claude Code hook).
- **Definition of done:** 10 agents can submit pre-commit/test runs; the machine never
  oversaturates; everything is visible live and in history; presets switch budgets.

### Phase 2 — "Insight & resilience"
- A6–A7 (mise graph + manual steps), B6 (memory-aware admission), C4 (dedup/join),
  D6–D7 (ETA + step drill-down/pie), E4–E7 (stats, charts, problem detection, retention),
  F3 (per-process attribution), G3–G5 (active preemption + actionable notifications),
  H3 (per-origin policies), J1/J3 (tracked steps + breakdown UI), L3 (idempotent submit).

### Phase 3 — "Caching & daemons"
- I1–I4 (fingerprint cache + dedup), J2 (recursive steps), K1–K2 (daemon manager / systemd GUI),
  B7 (allowlist/authorization).

---

## 11. Success metrics

- **Reliability:** spurious/flaky test failures attributable to contention drop to ~0 under N agents.
- **Saturation safety:** machine never enters sustained swap-thrash while orchestrator is governing.
- **Visibility:** 100% of proxied runs appear live and in history with logs + timing + origin.
- **Optimization:** ability to produce per-task p50/p95 per repo and show a before/after delta
  after a pipeline change.
- **Adoption (self):** all agent build/test/lint/pre-commit hooks route through the orchestrator.
- **Overhead:** orchestration overhead (queue+enforcement) is a small, measured % of run wall-clock.

---

## 12. Open questions / decisions needed

1. **Allocation model (B3):** hard cpuset pinning vs. CPUQuota vs. CPUWeight (fair-share)?
   Pinning is predictable but wastes idle cores; weight maximizes utilization but is less
   isolating. (See §13.2 — leaning weight+global-cap with optional pinning for e2e.)
2. **Memory enforcement policy (B4/B6):** `memory.high` (throttle) vs `memory.max` (OOM-kill)
   per task — and how to react when a task legitimately needs more than granted.
3. **Transport (§9):** HTTP+WS vs UDS-RPC for the submit/stream/control surface.
4. **Preemption policy (G3):** what defines "least important" (priority? age? resource hog?
   cache value?) and the pause mechanism (cgroup freezer vs SIGSTOP vs kill+requeue).
5. **Fingerprint contract (I1):** exactly what the caller must supply for a cache hit to be
   *correct*; how to avoid stale/incorrect cache serves. This is the riskiest later feature.
6. **mise depth (A6/J):** proxy-opaque vs graph-introspecting, and how step boundaries map to
   resource slices.
7. **Privilege model:** can we do all cgroup work via the user's systemd session
   (`systemd-run --user`, delegated slices) without root? (See §13.2.)
8. **Persistence scale:** DB choice + log storage (inline vs files + references) and retention.
9. **Daemon scope (K):** thin systemd wrapper vs independent supervisor.

---

## 13. Risks & mitigations

### 13.1 What's genuinely hard (risk register)

- **Preemption correctness (G3).** Pausing/killing under pressure without corrupting work or
  creating restart storms. Mitigate: start with *admission back-pressure only* (MVP), add
  freezer-based pause before kill, requeue cleanly.
- **Memory accounting & bursts.** Granting memory when demand is 15× bursty risks both OOM and
  underutilization. Mitigate: `memory.high` throttle as shock absorber + history-based projection.
- **Fingerprint/cache *correctness* (I).** A wrong cache hit = silently shipping on stale results.
  Mitigate: keep it opt-in, caller-supplied hash, conservative freshness, easy invalidation; ship last.
- **Long-lived blocking connections at scale.** Many agents blocked for minutes. Mitigate:
  heartbeats, decouple "run lifetime" from "client connection," resumable subscribe.
- **Overhead.** Spawning a scope + sampling per run must stay cheap relative to multi-second tasks (it is).
- **mise graph coupling (A6/J).** mise internals/JSON shape may shift. Mitigate: opaque-proxy MVP first.

### 13.2 mise compatibility — feasible

mise supports file/TOML tasks with `depends`/`wait_for`, machine-readable `--json`, and
`mise tasks deps [--dot]`. So: **MVP** proxies `mise run <task>` opaquely; **P2** introspects
the dependency graph to render steps and (optionally) slice resources per step.

### 13.3 Caching — promising but deferred for a reason

The Turborepo model (`SHA256` over source+test+config+deps+command → local/remote cache hit)
is proven and directly transferable to "two worktrees, same inputs → one run." But getting the
input set *exactly right* is the entire correctness burden, and a bad hit is worse than no cache.
Hence Pillar I is **P3**, opt-in, and conservative.

---

## 14. User stories & acceptance criteria

Concrete, testable stories that pin down the MVP. Each maps to the pillars/requirements above.

- **US-1 — See running tasks** *(D1–D2)*. Dashboard lists every running task with name, agent,
  repo, path, status, start time, duration, CPU, memory, tags; updates in real time.
- **US-2 — See queued tasks** *(C2)*. Queue shows position, **reason for waiting**, requested
  resources, agent, repo, and likely start order; queued tasks are cancellable.
- **US-3 — Stream task logs** *(D3)*. Click a running task → live stdout/stderr; logs persist after
  completion, are searchable, filterable by stream, downloadable; connection stable for long runs.
- **US-4 — Enforce max concurrency** *(B2, C1, C5)*. Configurable global **and** per-task-type
  concurrency; excess tasks queue and run when capacity frees.
- **US-5 — Prevent integration-test overload** *(C6)*. `integration-test` can be marked exclusive;
  extra runs block (agent waits, no failure); UI states it's waiting on an exclusive resource.
- **US-6 — Limit task CPU & memory** *(B1, B4, B5)*. Configurable global + per-task CPU/memory
  budgets, enforced at process start; a clear UI warning if limits can't be applied.
- **US-7 — Switch presets** *(H2)*. Presets (`interactive`/`balanced`/`heavy`/`overnight`)
  selectable from UI and CLI; they affect scheduling and limits.
- **US-8 — Detect system pressure** *(F2, G1–G3)*. Samples CPU/RAM/swap/disk against configurable
  thresholds; on breach can pause/cancel/block-new-starts — **only on owned tasks** — and shows
  which external processes are consuming resources.
- **US-9 — Record task history** *(E1–E2)*. Every accepted request becomes a persistent record;
  finished runs include duration, status, exit code, logs, resource rollups; failed runs easy to
  find; survives daemon restarts.
- **US-10 — Analyze slow tasks** *(E4–E6)*. Sortable history table; totals by task/repo/agent;
  per-task percentiles; slowest & failed runs; today-vs-previous-days comparison.
- **US-11 — Integrate with mise** *(A6, L1)*. Runs arbitrary commands with first-class
  `mise run <task>` examples; inspects available mise tasks when possible; records the mise task
  name; preserves mise env by running from the correct working directory.
- **US-12 — Deny unauthorized commands** *(B7)*. Allow task names/command patterns, deny patterns;
  denied tasks return a clear forbidden result, don't execute, and are recorded.
- **US-13 — Manual cancellation** *(A5)*. Cancel queued tasks; graceful terminate of running
  tasks with force-kill of the process tree on timeout; final state records the cancellation reason.
- **US-14 — Join duplicate requests** *(C4, I3)*. Optional cache/dedup key; a second request with a
  key already running **joins** the first; both callers get the same result; UI shows joined
  consumers; history records the join relationship.

---

## 15. Non-functional requirements & core values

These are the **quality attributes** the system must hold — the "how well," independent of any
single feature — and they are as binding as the functional requirements. The architecture
(`ARCHITECTURE.md`) is what *satisfies* them; each NFR notes how.

**Core values, one line each:** *frugal · reactive · robust · durable · self-cleaning ·
async-correct · flexible · observable · testable · local-by-default.*

- **NFR-1 — Performance & frugality.** A resource optimizer that itself hogs resources is
  self-defeating, so the orchestrator must be **lean**. *Targets:* small resident footprint at idle
  (daemon ≈ tens of MB RAM, ~0% CPU when idle); orchestration overhead (admission + enforcement +
  sampling) a low single-digit % of a task's wall-clock; CLI cold-start fast enough to be invisible
  in a hook (~tens of ms); sustain dozens of concurrent runs + many live stream subscribers without
  lag. *Satisfied by:* Rust/Tokio (no GC, static binary), cgroup-file sampling, SSE push.

- **NFR-2 — Reactivity / real-time.** State changes — `queued→running→done`, log lines, metrics,
  system pressure — propagate to every observer **near-instantly via push, not poll** (perceptually
  sub-second end-to-end). Real-time progress monitoring is a **first-class value**, not an add-on;
  the system is event-driven inside and out. *Satisfied by:* internal `watch`/`broadcast` event bus
  → SSE; no polling loops in the hot path; reactive admission/preemption on pressure signals.

- **NFR-3 — Robustness & reliability (long-lived daemon).** The daemon runs for days/weeks. A
  failing, hung, or misbehaving task must **never crash the daemon or take down the machine** —
  faults are isolated, and the system degrades gracefully (back-pressure, then preemption). It is
  **crash-only friendly:** on restart it reconstructs consistent state from the DB, with no run left
  in a torn state and **no orphaned child processes**. *Target:* zero daemon crashes from task
  faults; full recovery across restarts.

- **NFR-4 — Durability & data integrity (ACID).** All history and config is persisted
  **transactionally (SQLite WAL = ACID)** — no lost, duplicated, or corrupted records across
  restarts or power loss. State transitions are **atomic and consistent**: the run state machine is
  enforced and illegal transitions rejected; idempotent submission prevents double-enqueue; logs are
  durably written to disk with metadata in the DB. *Satisfied by:* SQLite WAL, a typed state machine,
  idempotency keys.

- **NFR-5 — Deterministic resource management & cleanup (RAII).** Because it is a long-lived daemon,
  **every acquired resource** — child processes, cgroup scopes, file descriptors, log writers,
  sockets, DB connections, channels, in-flight async tasks — is owned by a guard and released
  **deterministically (RAII / `Drop`)** on success, cancel, timeout, error, *or* shutdown. Killing a
  run reliably tears down its **entire process tree + cgroup** (no orphans); graceful shutdown drains
  and persists in-flight state. *Targets:* stable memory/fd/handle counts over multi-day uptime (no
  growth); zero orphaned processes or cgroups after cancel/crash.

- **NFR-6 — Asynchronous, concurrency-correct foundations.** Async-first (Tokio), built on
  **robust, deliberately-chosen concurrency primitives and data structures**: bounded channels with
  back-pressure, single-owner mutable scheduler state (no lock contention / deadlock), a typed state
  machine, immutable event records. Rust's type system guarantees **no data races**; every spawned
  task has a clear owner and cancellation path (structured concurrency). *Value:* invest in solid
  core types (`Run`, `RunId`, state & decision enums, channel topology) **before** features — the
  right primitives are the foundation everything else is built on.

- **NFR-7 — Flexibility & customizability.** Everything operationally meaningful is configurable
  **live, with no recompile or restart**: budgets, concurrency, per-task policies, exclusive locks,
  thresholds, presets, notifications, retention. Sensible hardcoded defaults, full override from the
  UI, instant apply. *Value:* the tool bends to the user's machine and workflow, not vice-versa.
  *Satisfied by:* config-as-data in SQLite (PRD §H, ARCH §17).

- **NFR-8 — Observability of itself & debuggability.** The governor must not be a black box:
  structured tracing/logs, a health/status surface, **recorded scheduler decisions + reasons**, and
  a debuggable API (curl-able SSE, OpenAPI/Scalar docs). Optional Prometheus export later.

- **NFR-9 — Maintainability & testability.** A pure, platform-free core (ports & adapters) makes the
  scheduler/policy **deterministically unit-testable** (fake clock, in-memory adapters); the
  no-platform-deps-in-core invariant is CI-enforced; changes stay local behind traits. (ARCH §13.)

- **NFR-10 — Security & least privilege (local).** Runs as the user, **no root by default** (cgroup
  governance via delegated user controllers); control plane is **local-only** (UDS + loopback+token,
  Origin-checked). Trusted single-user model — resource safety, not hostile-tenant isolation.
  (ARCH §19.)

- **NFR-11 — Scalability (single-machine envelope).** Handle the realistic ceiling — tens of
  concurrent agents, hundreds of queued runs, tens of thousands of history rows, multiple live
  dashboards — with indexed queries and bounded memory. Not distributed; "scale" = one fully-utilized
  workstation.

---

## 16. Sources

- AgentCgroup (eBPF resource control for AI agents) — https://github.com/eunomia-bpf/agentcgroup · https://arxiv.org/pdf/2602.09345
- CPU-centric perspective on agentic AI execution — https://arxiv.org/html/2511.00739v3
- cgroups v2 limits via systemd-run (CPUQuota / AllowedCPUs / CPUWeight / MemoryMax) — https://oneuptime.com/blog/post/2026-03-04-use-cgroups-v2-to-limit-cpu-and-memory-for-individual-processes/view · https://utcc.utoronto.ca/~cks/space/blog/linux/SystemdForMemoryLimitingII
- GNU Parallel semaphore & `--load`; make/Ninja jobserver — https://www.gnu.org/software/parallel/parallel_tutorial.html · https://www.gnu.org/software/make/manual/html_node/Job-Slots.html · https://blogs.gentoo.org/mgorny/2025/11/30/one-jobserver-to-rule-them-all/
- PM2 / supervisord / Logdy (process supervision + web UI + log streaming) — https://github.com/oxdev03/pm2.web · https://logdy.dev/blog/post/supervisorctl-logs-viewer-and-web-ui
- Turborepo / Bazel content-addressed caching — https://turborepo.dev/docs/crafting-your-repository/caching · https://bazel.build/remote/caching
- mise tasks (deps, JSON, parallel) — https://mise.jdx.dev/tasks/ · https://mise.jdx.dev/tasks/architecture.html · https://mise.jdx.dev/tasks/file-tasks.html
