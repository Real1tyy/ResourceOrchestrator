# Resource Orchestrator — Architecture & Tech Stack

> **Status:** Draft v0.1 · **Owner:** Real1ty · **Date:** 2026-06-20
> **Companion to:** `docs/PRD.md` (the *what/why*). This doc is the *how*.
> **Mental model:** `roch` is a tiny local **Kubernetes + systemd + Grafana hybrid, scoped to
> agent-triggered dev tasks** — one daemon, one CLI, one local web UI, one SQLite DB, and
> Linux-native process/resource controls. Not a desktop app, not a SaaS, not a CI server.

---

## 1. Guiding decisions (settled)

1. **One daemon is the brain.** All execution, queueing, policy, supervision, resource control,
   metrics, and history live in a single long-running user-level daemon. The CLI and the web UI
   are thin clients. The CLI must **never** execute the task itself — that would defeat resource
   control.
2. **CLI-first control plane over a Unix domain socket.** Agents and hooks talk to the daemon
   through the `roch` CLI, which speaks to it over a **Unix socket** in `$XDG_RUNTIME_DIR`.
   `roch run` supports **blocking** (default — submit, stream, exit with the task's code) and
   **non-blocking/detached** modes. This is the superior path for agents: local-only, no port,
   no auth dance, trivially droppable into a hook.
3. **The browser uses the same API over loopback TCP.** The daemon serves **one Axum app on two
   listeners**: the UDS (for CLI/agents) and `127.0.0.1:<port>` (for the browser). Same routes,
   same handlers, same SSE streams — no second protocol to maintain.
4. **Logically separate frontend/backend, physically one artifact.** `apps/web` (React) and
   `crates/roch-*` (Rust) are separate source packages. In production the daemon serves the
   pre-built static UI (embedded). Dev runs Vite + daemon side by side. **No Electron. No Tauri
   for MVP** (a thin Tauri tray wrapper is a possible *later* nicety, never a requirement).
5. **SSE for streaming, request/response for commands.** One-way streams (logs, metrics, events,
   status) use **Server-Sent Events** — simpler than WebSocket, browser auto-reconnect, debuggable
   with `curl`. WebSocket is added later *only* if we need bidirectional/interactive control
   (e.g. an attached terminal).
6. **Resource control ramps in levels** (§7): concurrency + process groups + nice/ionice first,
   then systemd transient scopes (cgroup v2), then direct cgroup/D-Bus. Queueing and visibility
   are valuable on their own — the product is never blocked on perfect cgroup support.
7. **Platform access lives behind ports (traits); the core is OS-agnostic.** Linux is the *only*
   implementation today, but every OS/hardware touchpoint — process spawning, resource limits,
   metrics, pressure signal, notifications, the control socket, even config paths — is reached
   through a trait defined in the pure core and implemented by a Linux **adapter**.
   `#[cfg(target_os)]` and platform crates are confined to adapter crates + the composition root —
   **never** the domain, scheduler, or executor logic. This keeps us un-coupled so a **macOS**
   adapter (later) or **broader Linux distro/hardware** support (much later) can slot in without
   touching the brain. **Windows is not a target.** To be clear: the MVP builds, tunes, and tests
   for **this exact Ubuntu box only** (§3) — the ports are cheap decoupling insurance, *not*
   multi-platform support we implement now. Full treatment in **§13**.
8. **No config files; config is data.** Defaults are hardcoded constants in source; everything
   operational is edited in the frontend Settings and persisted in SQLite (applied live, no
   restart). No TOML/YAML/dotfiles. Only a handful of bootstrap values (bind/port/socket/db path)
   come from CLI flags/env. See **§17**.

> These resolve PRD §12 open questions **#3 (transport), #7 (privilege model)**, and set the
> direction for **#1, #2, #4**.

---

## 2. Architecture shape

```
   Claude / Codex / terminal agents               Browser (dashboard)
            │                                            │
            │ roch run --task pre-commit \               │ http://127.0.0.1:47777
            │          -- mise run pre-commit            │ (REST + SSE)
            ▼                                            ▼
   ┌──────────────────┐                          ┌──────────────────┐
   │  roch CLI        │  HTTP-over-Unix-socket    │  static SPA      │
   │  - submit task   │ ───────────────┐          │  (served by      │
   │  - block/detach  │                │          │   the daemon)    │
   │  - stream logs   │                │          └────────┬─────────┘
   │  - exit w/ code  │                │                   │
   └──────────────────┘                ▼                   ▼
                          ┌───────────────────────────────────────────────────────┐
                          │  roch daemon  (single process, Tokio)                  │
                          │                                                        │
                          │  ┌─────────────┐   one Axum app, two listeners:        │
                          │  │  API layer  │   • $XDG_RUNTIME_DIR/roch/roch.sock    │
                          │  │ (Axum + SSE)│   • 127.0.0.1:47777                    │
                          │  └──────┬──────┘                                        │
                          │   cmd(mpsc)│ events(broadcast) / status(watch)          │
                          │  ┌─────────▼─────────┐    spawn   ┌────────────────────┐│
                          │  │  Scheduler        │──────────► │  Executor /run     ││
                          │  │  owns queue +     │   events   │  systemd-run scope ││
                          │  │  budget + groups  │ ◄───────── │  → child proc tree ││
                          │  │  + policy + presets│           │  logs+metrics tee  ││
                          │  └───────┬───────────┘            └─────────┬──────────┘│
                          │   pressure│                                 │           │
                          │  ┌────────▼────────┐   ┌──────────┐  ┌──────▼─────────┐ │
                          │  │  SysMon         │   │  Store   │  │  Log manager   │ │
                          │  │  PSI + /proc +  │   │  SQLite  │  │  files + ring  │ │
                          │  │  notify-rust    │   │  (sqlx)  │  │  + SSE fan-out │ │
                          │  └─────────────────┘   └──────────┘  └────────────────┘ │
                          └───────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
                          Linux process tree inside a cgroup-v2 scope
                          (mise / cargo / pnpm / pytest / playwright …)
```

---

## 3. Target machine (verified) and what it dictates

Probed on the dev box (2026-06-20). These are **measured facts**, and they de-risk the design:

| Capability | Result | Design consequence |
|---|---|---|
| cgroup hierarchy | **cgroup v2 (unified)** | Use v2 primitives directly; no v1 fallback |
| systemd | **255** | `systemd-run --user` transient scopes available |
| kernel | **6.17** | Has `cgroup.kill`, `cgroup.freeze`, per-cgroup PSI |
| hardware | **32 cores / 31 GiB RAM / 22 GiB swap** | Concrete budgets; leave headroom for desktop+daemon |
| delegated controllers (user) | **`cpu memory pids`** | CPU weight/quota + memory limits work **without root** |
| **NOT** delegated | **`cpuset`, `io`** | **Core-pinning + cgroup I/O weight need root** → make them opt-in |
| PSI | **present** (`/proc/pressure/*`) | Pressure signal for guardrails/preemption |
| freeze / kill | **`cgroup.freeze`, `cgroup.kill` present** | Clean pause/resume + orphan-free kill |
| `systemd-run --user --scope -p CPUWeight -p MemoryMax` | **works, no root** | The easy robust path is viable today |

**The single most important implication:** default resource governance uses **`CPUWeight`
(fair-share) + `CPUQuota` (optional hard ceiling) + `MemoryHigh`/`MemoryMax`** — all of which run
**without root** on this machine. **`cpuset` core-pinning** (and cgroup `io.weight`) require root,
so they become an **opt-in "strict isolation" mode** behind a privileged helper — never an MVP
requirement. This also happens to be the *better* default given the PRD's burstiness finding
(weights absorb 15× spikes better than rigid pinning).

> **Scope note:** everything in this section informs the **Linux adapter** (§13), reached only
> through platform-neutral ports. The core/scheduler never sees a cgroup or `/proc` path — so these
> machine specifics can't leak into the portable brain.
>
> **MVP scope:** the MVP is deliberately built and tuned for *this exact Ubuntu machine* —
> opinionated, not generalized. Portability (macOS, then broader Linux distros/hardware) is design
> *intention* held in the ports and executed much later; **Windows is not a target**. We don't
> generalize, test, or carry compatibility weight for other platforms now.

---

## 4. Technology stack (final)

| Layer | Choice | Notes |
|---|---|---|
| Daemon + CLI language | **Rust** | Long-running, low-overhead, no GC; close to Linux primitives; your primary lang |
| Async runtime | **Tokio** | Process supervision + streaming + timers |
| HTTP/SSE server | **Axum** + Tower / tower-http | Serves UDS **and** loopback; SSE responses; static UI |
| CLI | **Rust + clap** | Same workspace; ships as `roch` (daemon + client subcommands) |
| Database | **SQLite (WAL)** via **`sqlx`** | Embedded, single file, readers don't block writers; raw SQL for analytics |
| Logs | **files on disk** + metadata in SQLite | Never store raw logs as BLOBs |
| Resource control | **`systemd-run --user --scope`** (shell-out MVP) → **`zbus`** D-Bus → direct cgroup | Phased; see §7 |
| System metrics | **`sysinfo`** + **`procfs`/`/proc`** + **PSI** + per-cgroup files | sysinfo for global, cgroup files for per-task precision |
| Notifications | **`notify-rust`** (D-Bus/libnotify) | Phase 2; UI banners + CLI suffice for MVP |
| Frontend | **React + Vite + TypeScript** | Richest tables/charts ecosystem; you already do TS |
| Routing/data/tables | **TanStack Router + Query + Table** | Type-safe routes; server-state cache; headless history grid |
| Charts | **uPlot** (time-series) + lightweight lib (ECharts/Recharts) for pie/heatmap | uPlot = tiny, fast, canvas, built for streaming metrics |
| Terminal log view | **xterm.js** (optional, later) | Only if ANSI/terminal fidelity matters; virtualized list first |
| Styling/components | **Tailwind + shadcn/ui** | Fast, dense dashboard |
| API client + types | **`utoipa` (+ `utoipa-axum`) → OpenAPI → Orval** | Generates the typed client **and** TanStack Query hooks/keys/mutations/errors from the spec; free interactive docs (`utoipa-scalar`). Supersedes `ts-rs` |
| UI embedding | **`rust-embed`** | Bakes `apps/web/dist` into the binary → one artifact |
| Crates (support) | serde, serde_json, thiserror, anyhow, tracing(+subscriber), uuid, time, clap | Standard backbone |

**Deliberately *not* used (and why):** Docker/Podman for MVP (breaks native mise/worktree/path
flows, adds overhead for no isolation we need); Kubernetes/Nomad (cluster schedulers, absurd
overkill for one box); Prometheus/Grafana as the *core* (host-metrics tooling, not task
governance — we may *export* `/metrics` later); Electron (defeats a resource-saving tool);
SeaORM/heavy ORM (we want raw analytical SQL); caching-first (correctness-dangerous — explicit
dedup-by-key only, see PRD Pillar I).

---

## 5. Why these choices (condensed)

- **Rust daemon, not Node/Python.** The thing runs forever and must be frugal and robust at
  process supervision, signal handling, cgroup/systemd integration, and long-running sampling.
  Rust gives a small static binary, no GC pauses, strong typing, fearless concurrency. Node/Python
  are weaker exactly where this product is hard (Linux process/resource lifecycle). Go is a valid
  alternative but Rust wins on footprint and matches your stack.
- **SQLite WAL + SQLx.** Local, single-machine, append-heavy. WAL lets the UI read history while
  the daemon writes events — no reader/writer blocking. SQLx gives async + compile-checked raw SQL,
  ideal for the analytics queries (percentiles, per-repo/agent rollups) without ORM magic.
- **SSE-first.** Logs/metrics/events/status are one-way. `EventSource` reconnects automatically,
  is trivial to debug, and is less stateful than WebSocket. Commands are plain request/response.
- **React + TanStack + uPlot.** The UI has heavy tables, filters, live state, and time-series
  charts. This stack handles all four without inventing anything; uPlot keeps the charts cheap.

---

## 6. Control plane, transport & the CLI protocol

### 6.1 One API, two listeners

The daemon builds a single Axum `Router` and serves it on:

- **Unix domain socket** — `$XDG_RUNTIME_DIR/roch/roch.sock` (mode `0600`). **Primary plane for
  CLI/agents/hooks.** Local-only by construction; filesystem permissions *are* the auth; no port,
  no token, no origin checks needed.
- **Loopback TCP** — `127.0.0.1:47777` (configurable). **For the browser** (and curl/debugging).
  Bound to loopback only; protected by a **local token** (random, persisted in SQLite, injected
  into the served UI) and an **Origin allowlist** so a rogue page can't drive it.

Both speak HTTP, so the CLI uses **HTTP-over-UDS** (hyper supports a UDS connector) and reuses the
exact same routes the browser hits. No divergence. (The local socket sits behind a
`ControlTransport` port (§13) — UDS now (and on macOS later); swappable without touching call
sites if a platform ever needs a different local transport.)

### 6.2 CLI ↔ daemon: blocking and non-blocking

`roch run` is the agent's blessed entry point and is the *only* "blocking" client:

```bash
# BLOCKING (default): submit, stream logs to our stdout/stderr, exit with the task's exit code.
roch run --task pre-commit --repo "$PWD" --agent "$CLAUDE_SESSION_ID" --tag rust \
  -- mise run pre-commit

# NON-BLOCKING / detached: print the run id and return 0 immediately.
roch run --detach --task test -- mise run test           # → run id

# Re-attach to a detached/earlier run and block again (resumable stream).
roch attach <run-id>

# Other client subcommands (all over the UDS):
roch ps                  # running + queued
roch logs <id> [-f]      # replay / follow
roch cancel <id>
roch stats [--task ...] [--repo ...]
roch preset heavy        # switch active profile
roch system              # live node view (text)
```

**Mechanics of blocking mode.** `roch run` does `POST /api/tasks` over the UDS with
`?block=true`; the daemon holds the response open as an **SSE stream** that emits queue/admission
events, then log lines, then a terminal `result` event carrying the exit code. The CLI mirrors
output and exits with that code. **Crucially (PRD A4): the run is owned by the daemon, not the
connection.** If the CLI dies or the agent is killed, the task keeps running; `roch attach`
re-joins the stream. A detached submit is the same call with `block=false`.

This is exactly what makes it ideal in hooks: a Claude Code hook replaces the expensive command
with `roch run -- <cmd>`; the agent process blocks and sleeps until the result returns, consuming
no CPU while waiting.

### 6.3 HTTP surface (shared by CLI and browser)

```
POST   /api/tasks                 # submit (query: block, stream); returns id or streams to terminal
GET    /api/tasks?state=running   # list (state, repo, agent, task, tag filters)
GET    /api/tasks/:id             # detail + metrics rollup
POST   /api/tasks/:id/cancel
GET    /api/tasks/:id/logs        # replay (supports range/since)
GET    /api/tasks/:id/events      # SSE: status + log lines + metrics for one run
GET    /api/events                # SSE: global stream (queue, admissions, pressure, presets)
GET    /api/system                # current node snapshot
GET    /api/system/stream         # SSE: live system metrics
GET    /api/stats                 # aggregations (percentiles, by repo/agent/task/time)
GET    /api/config                # effective config
PUT    /api/preset/active         # switch preset
```

---

## 7. Linux resource-control layer (the hard part — ramped)

This is the riskiest subsystem, so it is **levelled**. Each level is independently shippable.

> **This whole section is the Linux *adapter* of the `ResourceController` / `ProcessExecutor` ports
> (§13).** Level 1 (process groups + `nice`/`ionice`) doubles as the **portable
> lowest-common-denominator backend** a future macOS adapter would start from; Levels 2–3 are
> Linux-only enrichments exposed via the port's reported **capabilities**, so the scheduler
> degrades gracefully where cgroups don't exist.

### Level 1 — concurrency + process groups + priority (MVP)

Solves the *dominant* pain (uncontrolled parallelism) with zero cgroup risk:

- Global max-concurrent + per-task-type concurrency + **exclusive locks** (PRD C5/C6).
- Each task runs in its **own process group / session** so the whole tree can be signalled.
- Graceful **SIGTERM → grace window → SIGKILL** of the group; runtime **timeouts**.
- **`nice`** (CPU scheduling) and **`ionice`** (best-effort/idle classes — work without root).
- **PSI-based admission gate:** stop starting new tasks while system memory/CPU pressure is high.

### Level 2 — systemd transient scopes (cgroup v2, no root) — early Phase 2

Verified working on this machine, so low-risk to adopt next. Each run launches inside a transient
**user scope**:

```bash
systemd-run --user --scope --unit "roch-<run-id>" --same-dir \
  -p CPUWeight=<share> -p CPUQuota=<n*100>% \
  -p MemoryHigh=<soft> -p MemoryMax=<hard> \
  -- mise run pre-commit
```

- **CPU semantics (important):** `CPUQuota=400%` = "up to 4 cores of CPU *time*", **not** pinned to
  4 physical cores. `CPUWeight` is proportional share under contention. We default to
  **CPUWeight for fair-share + CPUQuota as an optional hard ceiling** — both root-free here.
- **Memory (two-tier):** **`MemoryHigh`** is the primary control (throttle + reclaim — the shock
  absorber for bursts); **`MemoryMax`** is the last line (may trigger the cgroup's OOM killer).
  Set `High` below `Max` so we lean on reclaim before killing.
- **Per-task metrics for free:** read the scope's `cpu.stat`, `memory.current`, `memory.peak`,
  and `memory.pressure` directly — cheaper and more accurate than summing PIDs.
- **Preemption primitives:** write **`cgroup.freeze`** to pause/resume a task under pressure
  (reversible, instant); **`cgroup.kill`** for orphan-free termination.

### Level 3 — direct cgroup v2 / systemd D-Bus (later)

Replace shell-out with **`zbus`** D-Bus calls to the systemd manager (create/stop transient units
programmatically; cleaner lifecycle, no fork/exec of `systemd-run`), and/or direct cgroup file
management for the finest control. Defer — delegation/permission complexity isn't worth it until
Levels 1–2 are solid.

### Strict (opt-in) — core pinning & I/O weight (needs root)

`cpuset` pinning (`AllowedCPUs`) and cgroup `io.weight` aren't delegated to the user here. Offer
them as a **strict mode** via a small privileged helper (polkit/setuid or a root system service),
selected per-task (e.g. for flaky e2e suites that genuinely need isolation). Never required.

### Budget math (how the global pool maps to per-task limits)

- Operator sets a **pool**: `cores_budget` (e.g. 24 of 32) and `mem_budget` (e.g. 22 of 31 GiB,
  leaving headroom for desktop + daemon).
- **Admission** only starts a run if `Σ(granted_cores) ≤ cores_budget` **and**
  `Σ(granted_MemoryHigh) ≤ mem_budget` **and** a slot/concurrency-group is free.
- Per task: `CPUQuota = granted_cores × 100%`, `CPUWeight ∝ granted_cores`,
  `MemoryHigh/Max` from the task's profile (history-projected later, PRD B6).
- Example (PRD's "16 cores / 5 tasks"): 4 admitted at 3 cores + 1 at 4 = 16; the rest queue.

---

## 8. Scheduler design (pure, testable)

The scheduler is **pure Rust logic with no knowledge of Axum, SQLite, systemd, or React** — it
takes a snapshot of state and emits decisions. This is what makes it unit-testable.

```
Inputs:  queued runs, active runs, active preset, policies,
         resource pool, concurrency groups, current system pressure
Output:  Decision = StartRun(id, granted_limits)
                  | KeepQueued(id, reason)        // no slot / no budget / exclusive lock
                  | Reject(id, reason)            // allow/deny policy
                  | Preempt(id) / Freeze(id) / Resume(id)   // pressure response (Phase 2+)
```

**MVP rules:** respect global + per-type concurrency; honor **exclusive locks** (capacity-1 groups
→ contenders wait with reason `exclusive_lock`); respect the active preset's budgets; **don't
start new tasks under system pressure**; FIFO within a priority tier.

**Later:** priority tiers, fairness by repo/agent, shortest-job-first from history, ETA-aware
ordering, **dedup/join** of identical fingerprints (PRD C4/I3).

**Representative tests (no I/O):**

```
given 5 queued, max_concurrent=2, 0 running       → StartRun ×2, KeepQueued ×3
given an integration-test running, another queued → KeepQueued(reason=exclusive_lock)
given memory pressure = critical, capacity free   → KeepQueued(reason=system_pressure)
given pool=16 cores, 5 admitted                   → grants 4×3 + 1×4 = 16
```

The run **state machine** (queued → running → succeeded/failed/killed/timed_out/cancelled, plus
rejected/cache_hit/joined and the running⇄paused preemption edge) is defined in **PRD §7**; the
scheduler decides transitions, the executor performs them, the store records them.

---

## 9. Execution & log management

**Executor (one Tokio task per run):**
1. Receive `StartRun(id, limits)` from the scheduler.
2. Launch the command in its own process group, inside a Level-2 scope (or plain group at Level 1).
3. Spawn **stdout** and **stderr** reader tasks. Each line is fanned out to: the **combined log
   file**, an **in-memory ring buffer** (recent lines for instant replay), and a **`tokio::broadcast`
   channel** that feeds SSE subscribers (CLI + browser).
4. A **metrics sampler** ticks ~1 Hz: reads the scope's cgroup files (or PID tree at Level 1),
   updates live state (watch channel), and periodically rolls up.
5. On exit/cancel/timeout: finalize the record (exit code, durations, peak/avg metrics), close
   logs, emit the terminal event.

**Log storage layout:**

```
$XDG_DATA_HOME/roch/logs/<YYYY-MM-DD>/<run-id>.combined.log   (+ .stdout/.stderr if split)
```

SQLite stores only **log metadata** (path, byte/line counts, first/last timestamps, truncation
flags) — never the raw bytes. Retention evicts by age/size (PRD E7). **Never** write every log line
into SQLite (DB churn).

---

## 10. Metrics & system monitor

- **Global node view:** `sysinfo` for CPU/mem/swap/disk/process list, refreshed on a kept-alive
  `System` instance (CPU% needs deltas between refreshes — instantiate once, refresh over time).
  Augment with **PSI** (`/proc/pressure/{cpu,memory,io}`) as the canonical "under pressure" signal
  (catches swap thrash earlier than load average) and `/proc` for per-core/swap detail.
- **Per-task:** at Level 2, read the scope's cgroup files directly (`cpu.stat`, `memory.current`,
  `memory.peak`, `*.pressure`). At Level 1, walk the PID tree.
- **Sampling cadence:** ~1 s. **Persistence strategy:** live samples in an **in-memory ring
  buffer** (for live charts), **downsampled** short-term samples optionally to SQLite, **final
  rollup** (peak/avg CPU+mem, durations) to SQLite at task end. No always-on per-sample writes.
- **Guardrails (PRD G1–G3):** thresholds on CPU/RAM-remaining/swap/PSI/disk-free → notifications
  (`notify-rust`) + admission gate; under sustained pressure, **freeze then (if needed) kill** the
  least-important *owned* tasks (never external — PRD principle P2), resume when clear.

---

## 11. Persistence & data

**SQLite (WAL)** holds: task runs, task events, queue/policy decisions, **all settings / presets /
policies (§17) and the browser API token**, resource rollups, and (optionally) materialized stats.
**Files** hold only raw logs. **No config files** (§17). Layout:

```
$XDG_DATA_HOME/roch/roch.db                # SQLite (WAL): runs+history + ALL config/settings + API token
$XDG_DATA_HOME/roch/logs/<date>/<id>.log   # raw logs (files; metadata in DB)
$XDG_CACHE_HOME/roch/                       # transient/cache (later)
$XDG_RUNTIME_DIR/roch/roch.sock            # CLI control socket (0600)
```

Bootstrap-only values (bind/port/socket/db path) come from CLI flags/env, not a file (§17).
Schema migrations via `sqlx::migrate!` (checked-in `migrations/`). Core tables: `runs`,
`run_events`, `run_metrics_rollup`, `settings`, `presets`, `policies`, `repos` (derived dim). Index
for the hot queries: history filters (state, task, repo, agent, time), and stats aggregations.

---

## 12. Internal event system

Daemon-internal only (local, single process — **no Kafka/NATS/Rabbit**, that would be absurd):

- **`mpsc`** — command queues (API → scheduler; scheduler → executors).
- **`broadcast`** — fan-out events to SSE subscribers (`TaskQueued`, `TaskStarted`, `TaskLogLine`,
  `TaskResourceSampled`, `TaskSucceeded/Failed/Cancelled`, `SystemPressureChanged`, `PresetChanged`,
  `PolicyRejected`).
- **`watch`** — latest-value state (current system snapshot, active preset) for cheap polling.
- **SQLite** — the durable record behind the volatile event stream.

---

## 13. Crate layout, ports & adapters (Ubuntu-first, decoupled for later portability)

**Principle (hexagonal / dependency inversion).** The domain depends on **abstractions, never on
the OS**. Every OS- or hardware-specific capability is a **port** (a trait defined in the pure
core); each platform supplies an **adapter** (an impl). Today there is exactly one adapter set —
Linux — but the brain (`roch-core`: scheduler, policy, state machine) is written against ports, so
a **macOS** adapter (later) or wider **Linux distro/hardware** support (much later) can be added
**without touching the core** — **Windows is explicitly not a target**. This is SOLID's Dependency-Inversion + Interface-Segregation applied to portability.

**Enforceable invariant (what keeps the promise honest):**
- `roch-core` (scheduler/policy/domain) has **zero platform crates** (`nix`, `cgroups`, `sysinfo`,
  `zbus`, `notify-rust`, …) and **zero `#[cfg(target_os)]`**. If it compiles for a target with no
  adapter, the abstraction is clean.
- `#[cfg(target_os)]` and all platform calls live **only** in adapter crates and the **composition
  root** (`roch-daemon`/`roch-cli`), where the right adapter is selected and injected.
- A CI check (deny platform crates in `roch-core`'s dependency tree) makes drift a build failure.

**Guardrail — abstract at the capability seam, don't over-engineer.** Ports are defined at the
level of *intent* ("limit this run to N cores", "pause it", "sample its usage", "notify the user"),
never mechanism ("write `cgroup.freeze`"). We ship **one real adapter** and validate the seam by
*compilation* (core builds platform-free) + tests against the trait — **not** by building unused
second-platform adapters now (that risks baking Linux assumptions into a falsely-"generic" interface).
Where a platform genuinely can't do something (no PSI on macOS, no hard mem cap), the port reports a
**Capability** and the scheduler **degrades gracefully** instead of pretending all platforms are equal.

### Port catalog (every OS/hardware touchpoint)

| Port (trait) | Responsibility | Linux adapter (now) | Future adapter (macOS / other Linux) | Cap-gated |
|---|---|---|---|---|
| `ProcessExecutor` | spawn in isolated group/session, wire stdio, signal the tree | `setsid`+pgid, tokio | macOS POSIX (other Linux: identical) | no |
| `ResourceController` | apply CPU/mem/io limits; freeze/resume; kill-all | cgroup v2 via `systemd-run`→D-Bus→files | macOS `taskpolicy`/QoS + `renice` (soft); other Linux: same cgroup v2 | **yes** |
| `SystemProbe` | global CPU/mem/swap/disk/load snapshot | `sysinfo` + `/proc` | `sysinfo` is cross-platform | no |
| `PressureSignal` | "system under pressure" reading | Linux **PSI** `/proc/pressure/*` | macOS: derive proxy from mem+swap+load | **yes** |
| `TaskMetrics` | per-run resource sampling | scope cgroup files | macOS: PID-tree via `sysinfo` | no |
| `Notifier` | desktop notification | `notify-rust` (D-Bus) | macOS `osascript`; or no-op + UI banner | **yes** |
| `ControlTransport` | local CLI↔daemon socket | Unix domain socket | macOS: same UDS | no |
| `PathsProvider` | config/data/runtime dirs | XDG (`directories`) | `directories` handles mac/win | no |
| `GitMetadata` | repo/worktree/branch/sha/dirty | shell `git` | same (portable) | no |
| `DiskUsageScanner` | free space + largest paths | `statvfs` + walk | per-OS free-space call | no |
| `TaskRunner` | discover/run tasks | `mise` CLI | also enables `just`/`make`/`npm` later | no |
| `Clock` | now/sleep (determinism) | real clock | — (test fake) | no |

> `TaskRunner` and `Clock` show the discipline future-proofs more than the OS: we can swap the
> **task runner** (mise → just/make) and make the **scheduler deterministically testable**, for the
> same reason — depend on abstractions, not concretions.

### Trait sketches (defined in `roch-core`, intent-level)

```rust
pub trait ProcessExecutor {
    async fn spawn(&self, spec: ExecSpec) -> Result<RunningHandle>;        // own process group
    async fn signal(&self, h: &RunningHandle, sig: Signal) -> Result<()>; // term/kill the tree
}
pub trait ResourceController {
    fn capabilities(&self) -> ResourceCaps;                               // what THIS platform enforces
    async fn apply(&self, h: &RunningHandle, lim: ResourceLimits) -> Result<ResourceHandle>;
    async fn freeze(&self, h: &ResourceHandle, on: bool) -> Result<()>;   // cgroup.freeze | SIGSTOP | Job
    async fn kill(&self, h: &ResourceHandle) -> Result<()>;               // cgroup.kill | tree-kill
}
pub trait SystemProbe    { async fn snapshot(&self) -> SystemSnapshot; }
pub trait PressureSignal { fn supported(&self) -> bool; async fn read(&self) -> Pressure; }
pub trait Notifier       { async fn notify(&self, n: Notification) -> Result<()>; }
pub trait TaskRepository { async fn insert(&self, t: NewRun) -> Result<Run>;
                           async fn set_state(&self, id: RunId, s: RunState) -> Result<()>; }
```

The scheduler consumes these as generics / `dyn` trait objects; it cannot name a cgroup, a `/proc`
path, or `systemd`.

### Workspace

```
resource-orchestrator/
├─ Cargo.toml                 # workspace
├─ crates/
│  ├─ roch-core/    # domain + scheduler + policy + state machine + ALL PORT TRAITS — PURE, no platform deps
│  ├─ roch-db/      # sqlx adapter: repositories + migrations
│  ├─ roch-linux/   # LINUX ADAPTER: process groups, cgroup/systemd, PSI/sysinfo, nice/ionice, notify, UDS
│  ├─ roch-exec/    # execution orchestration over the ProcessExecutor/ResourceController ports
│  ├─ roch-daemon/  # COMPOSITION ROOT: Axum (UDS+TCP), wires adapters→core, scheduler loop, embeds UI
│  └─ roch-cli/     # `roch` binary: clap, ControlTransport client
│  # future (much later): roch-macos/ — additional adapter crate, same ports. No Windows.
├─ apps/web/        # React + Vite dashboard → embedded into roch-daemon
├─ migrations/      # SQLite schema
└─ docs/            # PRD, ARCHITECTURE, ADRs
```

Start lean — don't split adapter crates prematurely; `roch-linux` holds all Linux adapters until a
second platform actually arrives. What matters from day one is the **dependency direction**
(core ← adapters) and the **no-platform-deps-in-core** invariant, not the crate count.

**Layering (dependencies point inward only):** API (Axum, DTO validation, SSE) → Application
(TaskService, QueueService, SchedulerService, PolicyService, MetricsService, ConfigService) →
Domain + Ports (`roch-core`) → Adapters (`roch-db`, `roch-linux`, git/mise/notify).

---

## 14. API contract & client generation

**Single source of truth = the Rust handlers.** The frontend needs far more than DTO types — it
needs a typed client *and* the TanStack Query wiring (hooks, query keys, mutations, invalidation,
typed errors). So we generate all of it from an OpenAPI spec rather than hand-rolling it.

```
Rust DTOs + #[utoipa::path] handlers  ──utoipa──▶  openapi.json  ──Orval──▶  typed client + TanStack Query hooks  ──▶  React
```

- **Backend:** annotate DTOs (`ToSchema`) and handlers (`#[utoipa::path]`), bind routes with
  **`utoipa-axum`** so the route table and the spec **can't drift**, and serve the live spec at
  `/api/openapi.json` plus interactive docs (**`utoipa-scalar`**) at `/api/docs` — a free API
  explorer for debugging the daemon.
- **Frontend:** **Orval** consumes `openapi.json` and generates the **typed fetch client + TanStack
  Query hooks** (`useTasks`, `useCancelTask`, query keys, `invalidateQueries`, typed
  request/response/error bodies). A custom Orval **mutator** injects the local token + base URL.
  This is the win over `ts-rs`: we hand-roll the data-fetching layer *zero* times, not just the types.
- **Keep in sync via a task:** `mise run gen:api` → dump `openapi.json` from the daemon (a tiny
  `--dump-openapi` path, no running server needed) → run Orval into `apps/web/src/api/generated/`.
  **Commit `openapi.json`** (deterministic, reviewable diffs; frontend regen needs no backend up).
  Generated client is a build artifact — never hand-edited.
- **Errors:** document a standard `ApiError` response schema on handlers so Orval emits typed errors.

**SSE caveat (important):** OpenAPI/Orval model the **request/response + command surface**, *not*
streaming. The live SSE streams (`/api/events`, `/api/tasks/:id/events`, `/api/system/stream`) stay
**hand-written `EventSource`** code either way — but we still get typed *payloads*: define the SSE
event envelope + payloads as **`utoipa` component schemas**, so Orval emits their TS types and the
~30-line hand-written stream parser deserializes into them. Net: codegen for the REST plane, a
sliver of glue for the push plane, **typed everywhere** — and `ts-rs` is no longer needed at all.

---

## 15. mise integration (3 levels)

- **Level 1 (MVP) — wrapper.** `roch run --task pre-commit -- mise run pre-commit`. Run the command
  verbatim from the correct working directory (preserving mise's env), record the mise task name.
- **Level 2 — discovery.** Use `mise tasks --json` / task info to surface known tasks in the UI.
- **Level 3 — subtask graph.** Use `mise tasks deps` to model parent→subtask trees, per-step
  timing/logs/resources, and the per-run breakdown pie (PRD Pillar J). **Don't auto-decompose in
  MVP** — just run and record.

We *wrap and proxy* mise; we never replace it (PRD non-goal).

---

## 16. Agent / hook integration

- **Wrapper-first (the reliable path for long tasks):** replace the expensive command in the
  agent's harness with `roch run -- <cmd>`. The agent blocks on the UDS, exit code propagates,
  logs stream. This is what makes long builds/tests safe.
- **Claude Code hooks** (exit-code + JSON control) are used to *redirect/guard*: block direct
  expensive commands, rewrite them toward `roch run`, attach metadata (repo/agent/tags), enforce
  allow/deny policy, and handle pre-stop verification. Hooks complement the wrapper; they don't
  replace it for long-running work.

---

## 17. Configuration — defaults in source, edited in the UI, persisted in SQLite (no config files)

**No TOML/YAML/dotfiles.** Configuration is **hardcoded factory defaults in Rust source**, seeded
into SQLite on first run, and from then on **everything is editable from the frontend Settings
menu** and persisted in the DB. Edits take effect **live** (pushed through the `watch` channel — no
restart). There is no on-disk config file to hand-edit or keep in sync.

- **Defaults (source of truth for the *first* run):** a `roch_core::defaults` module of plain
  constants — presets (interactive/balanced/heavy/overnight budgets), task policies, guardrail
  thresholds, retention, sampling cadence. On boot, if the `settings`/`presets`/`policies` tables
  are empty, seed them from these constants. **"Reset to defaults"** in the UI re-seeds from the
  same constants.
- **Runtime config (after first run):** lives only in SQLite, edited via Settings → `PUT /api/…` →
  row update → `watch` broadcast → scheduler/sysmon pick it up immediately. Surfaces: presets &
  active preset; per-task policies (allow/deny patterns, concurrency groups, exclusive locks,
  timeouts); guardrail thresholds; notification prefs; log retention.
- **Bootstrap-only (the chicken/egg minimum):** the few values needed *before* the DB is open —
  bind host/port, socket path, DB path — come from **hardcoded defaults overridable by CLI flags /
  env** (`roch daemon --port 47777 --db <path>`), never a file. Everything else is DB + UI.

Illustrative defaults *in source* (Rust constants, **not** a file):

```rust
// roch-core/src/defaults.rs  — seeds the DB on first run; "Reset to defaults" re-applies these.
// Budgets leave headroom on a 32-core / 31 GiB box for the desktop + daemon.
pub const PRESETS: &[Preset] = &[
    Preset { name: "interactive", max_concurrent: 2,  cores: 8,  mem_high_mb: 10_000, mem_max_mb: 13_000 },
    Preset { name: "balanced",    max_concurrent: 4,  cores: 16, mem_high_mb: 16_000, mem_max_mb: 20_000 },
    Preset { name: "heavy",       max_concurrent: 8,  cores: 26, mem_high_mb: 22_000, mem_max_mb: 26_000 },
    Preset { name: "overnight",   max_concurrent: 12, cores: 30, mem_high_mb: 26_000, mem_max_mb: 28_000 },
];

pub const POLICIES: &[Policy] = &[
    Policy::allow("pre-commit").max_concurrent(2).timeout_secs(900),
    Policy::allow("integration-test").exclusive(),                 // capacity-1 lock (optional per-repo scope)
    Policy::deny("playwright-full", "Full Playwright suite too expensive for automatic agent execution."),
];
```

Everything an operator can touch is reachable from the **Settings** UI; nothing requires editing a
file or restarting the daemon.

---

## 18. Frontend architecture

- **Pages:** Dashboard (running+queued), Queue, History (TanStack Table — sort/filter/paginate),
  Task detail (live logs + metrics + step breakdown later), System (node monitor), Stats, **Settings
  — everything configurable** (presets, policies, guardrail thresholds, notifications, retention),
  persisted to SQLite and applied live (§17). Type-safe routes via TanStack Router.
- **Data:** **Orval-generated TanStack Query hooks** (from the OpenAPI spec, §14) for all REST
  reads/commands; hand-written **`EventSource`** consumers for the SSE streams (`/api/events`,
  `/api/tasks/:id/events`, `/api/system/stream`), deserializing into utoipa-generated payload types.
- **Charts:** **uPlot** for CPU/mem/queue-depth/runtime time-series; a light lib for pie/heatmap.
- **Logs:** virtualized line list for MVP; **xterm.js** only if/when ANSI fidelity is wanted.
- **Served by the daemon** (embedded `dist/`); dev uses Vite with `/api` + `/sse` proxied to the
  daemon.

---

## 19. Security (local-first)

- **UDS** is the trust boundary for the CLI: `0600`, owner-only — no token needed.
- **Loopback TCP** for the browser: bind `127.0.0.1` only, require a **local token** (random,
  persisted in SQLite, injected into the served UI), validate **Origin**, reject remote. No
  `0.0.0.0`, ever.
- Trusted single-user model (PRD §3.3) — this is resource safety + organization, **not** a
  hostile-tenant sandbox.

---

## 20. Build, packaging & deployment

- **Single binary** `roch` (daemon + CLI subcommands) with the UI embedded via `rust-embed`.
- **Run as a user-level systemd service** (`systemd --user`): autostart, restart-on-failure,
  journald logging for the daemon itself, and a natural home for the user-scope delegation.
- **Dev:** `cargo run -p roch-daemon` + `pnpm --dir apps/web dev` (Vite proxy). **Prod:**
  `pnpm build` → embed → `cargo build --release` → one artifact.
- **Optional later:** a thin **Tauri** tray/window wrapper (system WebView, not Chromium) for a
  native window, tray icon, desktop notifications, and autostart toggles — purely cosmetic, never
  required.

---

## 21. MVP build order (maps to PRD §10 phasing)

- **MVP-1 — daemon + CLI + queue.** `roch daemon`; `roch run -- <cmd>` (blocking over UDS) and
  `--detach`; SQLite history; stdout/stderr log files; global + per-task concurrency + exclusive
  locks; process-group cleanup + SIGTERM/SIGKILL + timeouts + nice/ionice. *(PRD A1–A5, B8, C1–C3/C5–C6, E1–E3, L1–L2)*
- **MVP-2 — live UI.** Running/queued/detail pages; live logs + metrics via SSE; history table;
  system page (sysinfo + PSI). *(PRD D1–D5, F1–F2)*
- **MVP-3 — Linux governance.** systemd `--user` transient scopes: CPUWeight/CPUQuota +
  MemoryHigh/MemoryMax; per-cgroup metrics; freeze/kill; PSI admission gate. *(PRD B1–B6)*
- **MVP-4 — policies & presets.** allow/deny, presets (interactive/balanced/heavy/overnight),
  pressure-based no-new-starts + notifications. *(PRD G1–G3, H1–H2)*
- **MVP-5 — analytics.** slowest-today, counts, failure rate, p50/p95, runtime by repo/agent,
  trends. *(PRD E4–E6)*

Then PRD Phase 2/3: mise graph + step breakdown, dedup/join, ETA, active preemption refinement,
caching, daemon manager (Pillar K), strict cpuset mode.

---

## 22. Open architecture decisions (to record as ADRs)

1. **D-Bus vs shell-out for systemd scopes** — start shell-out (`systemd-run`), move to `zbus`
   when lifecycle control demands it (Level 2 → 3).
2. **Strict cpuset/io mode privilege** — polkit helper vs root system service vs skip. (Needs root
   here; defer.)
3. **Preemption "importance" ranking** — by priority? age? resource hog? cache value? (PRD §12.4)
4. **Stats: live SQL vs materialized rollups** — start live SQL, materialize if it gets slow.
5. **API contract: `ts-rs` vs `utoipa`+Orval** — **decided: `utoipa` → OpenAPI → Orval** (typed
   client + TanStack Query hooks); `ts-rs` dropped. SSE payload types come from `utoipa` component
   schemas; SSE plumbing stays hand-written. `utoipa-axum` binds routes to prevent spec drift.
6. **Fingerprint/cache contract** — deferred to PRD Phase 3; correctness-first, opt-in.
7. **Portability seam (decided).** All OS/hardware access goes through ports (traits) in `roch-core`;
   Linux adapters in `roch-linux`; `#[cfg(target_os)]` only at the composition root; a CI check
   denies platform crates in `roch-core`. Build new-platform adapters *only when targeted* — no
   speculation. (§13)
8. **Config storage (decided).** No config files. Hardcoded defaults seed SQLite; all settings are
   edited in the UI and persisted in the DB, applied live; bootstrap (bind/port/socket/db) via
   CLI/env only. (§17)

