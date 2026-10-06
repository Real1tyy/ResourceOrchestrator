---
type: note
id: note-01a1128f-c66d-7f53-98e8-50434ad50f66
status: processed
intended_for: agent
tags:
  - agenticd
  - resource-orchestrator
  - resource-management
  - caching
voice_url:
related_docs: []
created: 2026-10-06
updated: 2026-10-06
---



[Pasted 65 lines]
```
---
Title: "[[Voice/Resource Manager For Agent Pipelines|Resource Manager For Agent Pipelines]]"
Status: Inbox
Source: voice-memos
Source ID:
  - so_about_the_research_resource
  - maybe_as_part_of_that
Recorded At: 2026-06-19T07:36:12+02:00
Duration: 221
Summarizer: anthropic/claude-opus-5-5
Intent: reference
Areas:
  - software
Tags:
  - agenticd
  - resource-orchestrator
Goal:
  - "[[Goals/ResourceOrchestrator|ResourceOrchestrator]]"
  - "[[Goals/Agentic Workflows|Agentic Workflows]]"
Backlink Tags:
  - "[[Information/Agentic Engineering|Agentic Engineering]]"
Aliases: []
_ZettelID: 20260619073612
_Archived: false
_LastModifiedTime: 20261005220408
---
These two memos (2026-06-19) are the seed of [[Goals/ResourceOrchestrator|ResourceOrchestrator]] — its PRD is far more detailed, so go there for the current design. The problem: agents running pipeline harnesses max out CPU and RAM until the machine is unusable for me, so resource contention has to be brought under control.

## The Original Idea

- A global tool, maybe its own repository (possibly worth open-sourcing — it's a real bottleneck for everyone running agents).
- Works like RTK: hooked into pre-tool-use, every pipeline command is forwarded through a proxy that sees full CPU/RAM status.
- Configurable in a localhost UI: cores, RAM budget, number of concurrent sessions, presets, manual tweaking (conservative etc.).
- Derives allocation, e.g. 20 cores across 5 sessions -> 4 cores each.
- Per-toolchain limits: vitest, cargo, Python each have their own env vars (look them up via Context7) — or simply cap via the OS (opinionated, Ubuntu only).
- Records history and statistics in the localhost UI for analysis.

## Caching Identical Runs (2026-06-19)

- Multiple agents in multiple worktrees running the same `npm test` on the frontend -> run once, return the cached result to the rest.
- Cache key: a hash supplied by the downstream, e.g. based on the latest change in the target directory (or commit SHA); detecting dirty state is the hard part.
- Eviction after a while; show the cache in the UI.

## Already Captured In

- [[Goals/ResourceOrchestrator|ResourceOrchestrator]]
- [[Tasks/ResourceOrchestrator - Resource Governance Tooling|ResourceOrchestrator - Resource Governance Tooling]]

## Projects

**Flagged:** AgenticD

- [[Goals/ResourceOrchestrator|ResourceOrchestrator]]
- [[Goals/Agentic Workflows|Agentic Workflows]]

## Transcript

Merged from 2 recordings. Original audio is archived in Google Drive under `Voice Memos/<Source ID>/`.

> [!quote]- 2026-06-19 07:36 · so_about_the_research_resource (2m 30s)
> so about the research resource manager again so what we can do is build up a global you can create separate repository maybe fucking famous because this is quite a fucking bottleneck with agents running pipeline hardness just fucking mining your CPU and RAM so basically we could make it intelligent so it's going to be we have to figure out the best way to do it basically it could be a proxy for every single we could use it like RTK actually so it could be used like RTK we added to the pre-tool use and so whenever the pipeline is being run it just forwards to this proxy which can see and manages the it sees the entire CPU memory status everything and you can configure it in the localhost and localhost also offers statistics about the entire resources RAM and RAM storage CPU memory everything and you can make it configured conservative whatever you can configure how many cores you want to use how much RAM you want to dedicate you can create presets and just manually tweak it and so then it's going to basically control this can also create like how many sessions you want to run at the same time so it can be derived so for example you had on those 20 cores and I have five sessions it's going to allocate for on each call how many sessions to go through it's going to also record this and create statistics history for you so we can track it and analyze it and see how it worked so it's going to be a proxy which so whenever I forward any tool call it's going to look at it okay so this is vitest so it supports the environment variable this one cargo supports this one Python basically every single toolchain we need to figure out with the context seven how it is controlled or we can just copy it with actually always limits so it doesn't even have to be a better with you can just cap it with the operating system so it's going to be opinionated based on Ubuntu and then we just fucking forward it that way but basically this is very critical for the contention and smooth running of the computer because it's actually unusable for you you cannot actually do anything while there are multiple agents running so this is critically important to get right under control

> [!quote]- 2026-06-19 07:36 · maybe_as_part_of_that (1m 11s)
> maybe as part of that tool we could maybe also hash like the status of the this is getting more complex but like we could hash the statuses and cache it basically we want to cache it meaning so we have multiple agents from multiple vertices that call us about the frontend we just cache it because it's the same fucking npm test unless it changed based on if it was dirty which is complex we could at least use the commit cha or some shit like this is getting more complex basically the downstream could provide some hash so basically the downstream can provide a hash to showcase it to run deterministically so we can just hash over the file changes so by the latest change inside the entire directory so if i'm running vtest over the frontend i can just hash and figure out the latest change in the entire frontend directory commit cha and pass that in means the other tools running they are going to receive it you're going to cache it and return them with like an eviction after whatever this can also be displayed there and you can just work with it

```
