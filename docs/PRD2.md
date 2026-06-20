# PRD: Resource Orchestrator

## 1. Product Name

**Resource Orchestrator**

Working aliases:

* `roch`
* `resource-orchestrator`

## 2. One-line Summary

Resource Orchestrator is a local Ubuntu-only task execution gateway for agentic engineering workflows. It proxies expensive commands such as builds, tests, linters, formatters, pre-commit checks, and future daemon processes through a central service that controls concurrency, limits resource usage, streams logs, records history, and exposes real-time observability.

## 3. Product Thesis

Modern agentic engineering workflows run many coding agents in parallel. Each agent can independently run expensive commands such as `cargo test`, `cargo clippy`, Playwright, integration tests, database tests, formatters, and pre-commit pipelines.

Without coordination, ten agents can accidentally saturate CPU, memory, disk I/O, and ports. This causes slow feedback loops, flaky tests, false failures, timeouts, machine freezes, and wasted model time.

Resource Orchestrator solves this by becoming the local execution gateway for all agent-triggered tasks.

Instead of agents running commands directly, they submit task requests to Resource Orchestrator. The orchestrator queues, schedules, isolates, monitors, executes, records, and returns results.

## 4. Target User

### Primary user

A power developer running multiple local coding agents on Ubuntu, especially for Rust, TypeScript, Python, and future Go projects.

The primary user:

* Runs multiple Claude Code / Codex / terminal agent sessions in parallel.
* Uses isolated Git worktrees.
* Runs frequent harness checks: format, lint, build, test, pre-commit, pre-stop, verification loops.
* Uses `mise` as the task runner.
* Wants aggressive automation but cannot allow agents to overload the workstation.
* Wants observability into what every agent is doing.
* Wants historical statistics to optimize the slowest feedback loops.

### Secondary users

* Open-source developers running multi-agent local workflows.
* Developers with expensive monorepo verification pipelines.
* Developers who want a local “CI-lite” dashboard before pushing to CI.
* Developers who want better visibility into command history, runtime, logs, and resource usage.

## 5. Problem Statement

When several agents run expensive local commands independently, the developer loses control over:

* Which commands are currently running.
* Which agent started each command.
* Which repository/worktree each command came from.
* How many expensive tasks are running concurrently.
* How much CPU, memory, and disk I/O each task consumes.
* Whether failures are real or caused by resource starvation.
* Which tasks are slow, flaky, redundant, or overused.
* How much total time is being burned by harness loops.
* Whether identical work is being repeated by multiple agents.
* Whether the machine is approaching RAM exhaustion, swap thrashing, or disk exhaustion.

This creates false-negative test failures, poor agent throughput, and poor developer confidence.

## 6. Goals

### G1. Centralize task execution

All expensive agent-triggered commands should pass through Resource Orchestrator instead of being run directly.

Examples:

* `mise run pre-commit`
* `mise run test`
* `cargo test`
* `cargo clippy`
* `npm test`
* `pnpm lint`
* `pytest`
* `playwright test`

### G2. Control concurrency

The system must maintain a task queue and only allow a configured number of tasks to run at once.

Example:

* Maximum 5 concurrent tasks globally.
* Maximum 1 Playwright suite at a time.
* Maximum 2 Rust full builds at a time.
* Maximum 1 integration-test suite per repository.
* Maximum 1 database-dependent suite globally.

### G3. Control resource usage

The system must run tasks with resource constraints where technically possible.

MVP resource controls:

* CPU budget per task or per task group.
* Memory soft/hard limit per task or task group.
* Optional process priority adjustment.
* Optional I/O niceness.
* Kill or pause controlled tasks when the system is under pressure.

### G4. Provide real-time observability

The user must see:

* Running tasks.
* Queued tasks.
* Finished tasks.
* Task state.
* Runtime.
* Agent/session identity.
* Repository/worktree path.
* Task type.
* Tags.
* Live stdout/stderr logs.
* Approximate CPU/memory usage per task.
* Global system CPU/RAM/disk/swap usage.

### G5. Record history

Every task request must be persisted.

History must include:

* Task ID.
* Parent task ID if subtask.
* Task name.
* Command.
* Arguments.
* Working directory.
* Repository identity.
* Worktree path.
* Agent identity.
* Tags.
* Queue timestamp.
* Start timestamp.
* Finish timestamp.
* Duration.
* Exit code.
* Final status.
* Log location.
* Resource samples or rollups.
* Policy decision.
* Cache key if present.
* Deduplication group if present.

### G6. Analyze feedback loops

The system must help the user identify expensive and problematic tasks.

Statistics should include:

* Count by task name.
* Count by repository.
* Count by agent.
* Total runtime by task.
* Total runtime by repository.
* Median runtime.
* P75/P90/P95/P99 runtime.
* Min/max runtime.
* Failure rate.
* Flaky task candidates.
* Slowest tasks today.
* Most frequently run tasks.
* Most resource-heavy tasks.
* Queue wait time.
* Runtime trend over time.

### G7. Support mise-first workflows

The MVP must work cleanly with `mise run <task>`.

Resource Orchestrator should not replace mise initially. It should wrap and proxy mise.

Example:

```bash
roch run \
  --repo /home/real1ty/Programming/agentic-workflow \
  --agent claude-session-auth-01 \
  --task pre-commit \
  --tag rust \
  -- mise run pre-commit
```

### G8. Support blocking agent calls

Agents should submit a task and block until the result is ready.

Expected behavior:

1. Agent calls Resource Orchestrator.
2. Resource Orchestrator accepts or rejects the task.
3. If accepted, the task may enter the queue.
4. Agent process blocks.
5. Resource Orchestrator eventually runs the task.
6. Resource Orchestrator streams or buffers logs.
7. Resource Orchestrator returns final exit code, stdout/stderr summary, and result metadata.
8. Agent continues only after the result is available.

This prevents agents from starting more work while waiting for an expensive verification step.

## 7. Non-goals

### MVP non-goals

* Cross-platform support.
* Windows support.
* macOS support.
* Remote multi-machine scheduling.
* Kubernetes integration.
* Full container runtime replacement.
* Full CI system replacement.
* Perfect sandboxing against malicious commands.
* Killing arbitrary external system processes automatically.
* Perfect build-cache correctness for arbitrary shell commands.
* Replacing mise as the task runner.
* Replacing systemd for all long-running services.
* Cloud-hosted SaaS dashboard.

### Explicitly deferred

* Distributed execution across multiple machines.
* Shared remote cache.
* Team mode.
* Web login/auth.
* Plugin marketplace.
* Deep semantic test selection.
* Automatic codebase dependency graph inference.
* GPU resource scheduling.
* Fine-grained eBPF-based process tracing.
* Full daemon manager.

## 8. Product Principles

### P1. Git and task metadata are truth

Resource Orchestrator should record the repository path, worktree path, branch, commit SHA, dirty state, and task metadata whenever available.

### P2. Controlled tasks are safer than uncontrolled tasks

The orchestrator should only automatically pause/kill tasks it owns. External tasks should be detected and surfaced, but not killed automatically by default.

### P3. Scheduling beats brute force

The system should prefer queueing, fairness, resource budgets, and backpressure over letting every agent run immediately.

### P4. History enables optimization

The product is not only a task runner. It is a feedback-loop profiler. It should reveal which tasks waste the most developer and agent time.

### P5. Start simple, add correctness later

The MVP should not attempt perfect caching, perfect sandboxing, or full task graph inference. It should first solve queueing, observability, execution, logs, and history.

## 9. Core Concepts

### 9.1 Task Definition

A named command or workflow that can be executed.

Examples:

* `pre-commit`
* `pre-stop`
* `format`
* `lint`
* `test`
* `integration-test`
* `playwright`
* `cargo-check`
* `cargo-clippy`

### 9.2 Task Run

One concrete invocation of a task definition.

Example:

`pre-commit` run by `claude-session-auth-01` in `/repo/worktrees/auth-fix` at `2026-06-20T09:40`.

### 9.3 Subtask

A child run within a parent task.

Example:

`pre-commit` contains:

* `format`
* `lint`
* `cargo-check`
* `cargo-test`

Subtasks may run sequentially or in parallel depending on configuration.

### 9.4 Agent

The process or session that requested a task.

Examples:

* Claude Code session.
* Codex session.
* Manual terminal.
* CI-like local script.
* Future custom agent.

### 9.5 Repository

The logical project root.

### 9.6 Worktree

The concrete working directory from which a task was requested.

### 9.7 Resource Pool

A configured budget of CPU, memory, concurrency, and optionally I/O.

Examples:

* `interactive`
* `balanced`
* `heavy`
* `overnight`

### 9.8 Policy

Rules deciding whether a task is allowed, queued, limited, denied, cancelled, or requires manual approval.

### 9.9 Cache Key

A user-provided or system-derived fingerprint representing whether a task result can be reused.

### 9.10 Daemon

A long-running process managed or monitored by Resource Orchestrator.

This is future scope.

## 10. User Stories

### US1. View currently running agent tasks

As a developer, I want to see every task currently running across all agents so that I know what my machine is doing.

Acceptance criteria:

* The dashboard shows running tasks.
* Each task shows name, agent, repo, path, status, start time, duration, CPU, memory, and tags.
* The list updates in real time.

### US2. View queued tasks

As a developer, I want to see tasks waiting in the queue so that I understand why an agent is blocked.

Acceptance criteria:

* The dashboard shows queued tasks.
* Each queued task shows queue position, reason, requested resources, agent, repo, and estimated start order.
* User can cancel queued tasks.

### US3. Stream task logs

As a developer, I want to click a running task and see live stdout/stderr logs.

Acceptance criteria:

* Logs stream in real time.
* Logs are preserved after task completion.
* Logs can be searched.
* Logs can be filtered by stdout/stderr.
* Logs can be downloaded or copied.
* Log view keeps connection stable for long-running commands.

### US4. Enforce max concurrency

As a developer, I want to limit how many expensive tasks can run at once.

Acceptance criteria:

* User can configure global maximum concurrent tasks.
* User can configure per-task-type maximum concurrent tasks.
* Extra tasks are queued.
* Queued tasks eventually run when capacity is available.

### US5. Prevent integration test overload

As a developer, I want only one integration test suite to run at a time because parallel integration tests make my repo flaky.

Acceptance criteria:

* User can define `integration-test` as exclusive.
* Additional `integration-test` runs are queued.
* The agent receives a blocking wait rather than a failure.
* UI explains that the task is waiting for an exclusive resource.

### US6. Limit task CPU and memory

As a developer, I want Resource Orchestrator to run tasks with CPU and memory budgets so that agents do not freeze my computer.

Acceptance criteria:

* User can configure global task CPU budget.
* User can configure global task memory budget.
* User can configure per-task defaults.
* Resource Orchestrator applies resource limits when starting the process.
* If limits cannot be applied, the UI shows a clear warning.

### US7. Switch presets

As a developer, I want quick presets for resource allocation.

Acceptance criteria:

* Presets can be selected from the UI.
* Presets can be selected from CLI.
* Presets affect queue scheduling and resource limits.
* Built-in presets:

  * `interactive`
  * `balanced`
  * `heavy`
  * `overnight`

Example preset behavior:

* `interactive`: fewer cores, lower concurrency, protect desktop responsiveness.
* `balanced`: moderate concurrency.
* `heavy`: high concurrency, high CPU allocation.
* `overnight`: maximum safe resource usage.

### US8. Detect system pressure

As a developer, I want the system to protect the workstation when RAM, swap, or CPU pressure is dangerous.

Acceptance criteria:

* Resource Orchestrator samples global CPU, RAM, swap, and disk.
* User can configure thresholds.
* When pressure exceeds threshold, Resource Orchestrator can pause, cancel, or prevent new task starts.
* MVP should only auto-act on tasks owned by Resource Orchestrator.
* UI shows which external processes appear to be consuming resources.

### US9. Record task history

As a developer, I want all task runs recorded so I can debug and optimize my workflow.

Acceptance criteria:

* Every accepted task request creates a persistent task run record.
* Finished task runs include duration, status, exit code, logs, and resource rollups.
* Failed tasks are easy to find.
* Historical data persists across daemon restarts.

### US10. Analyze slow tasks

As a developer, I want to see which tasks waste the most time.

Acceptance criteria:

* History page shows sortable task table.
* Stats page shows total runtime by task, repo, and agent.
* Percentiles are visible per task.
* Slowest runs are visible.
* Failed runs are visible.
* User can compare today versus previous days.

### US11. Integrate with mise

As a developer, I want to keep using `mise run pre-commit`.

Acceptance criteria:

* Resource Orchestrator can run arbitrary commands.
* Resource Orchestrator has first-class examples for `mise run <task>`.
* Resource Orchestrator can inspect available mise tasks when possible.
* Resource Orchestrator records the mise task name.
* Resource Orchestrator preserves mise environment behavior by running from the correct working directory.

### US12. Deny unauthorized commands

As a developer, I want to allowlist which expensive commands agents may run.

Acceptance criteria:

* User can define allowed task names.
* User can define allowed command patterns.
* User can deny command patterns.
* Denied tasks return a clear forbidden result.
* Denied tasks are recorded in history.
* Denied tasks do not execute.

### US13. Manual task cancellation

As a developer, I want to cancel a running or queued task.

Acceptance criteria:

* User can cancel queued tasks.
* User can request graceful termination of running tasks.
* If graceful termination times out, user can force-kill the process tree.
* Final state records cancellation reason.

### US14. Join duplicate task requests

As a developer, I want duplicate task requests to share one execution when safe.

Acceptance criteria:

* A task can include an optional cache/deduplication key.
* If a task with the same key is already running, the second request joins the first.
* Both callers receive the same final result.
* UI shows joined consumers.
* History records the join relationship.
