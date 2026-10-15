---
type: note
id: note-01a1409c-05e2-7440-a950-d21efcba2526
status: processed
intended_for: agent
tags:
  - agenticd
  - periodic-scheduler
  - resource-management
voice_url:
related_docs: []
created: 2026-10-15
updated: 2026-10-15
---

Add Chrome Periodic Scheduler Support To Agenticd

2026-10-13 10:52 · 1:21 · 338.0 KiB · plaud_api

tags: Agenticd, Software

## Summary

- We want to add Chrome periodic scheduler support to Agenticd with dedicated data structures, UI, and flows.
- The scheduler will let us configure and run my tasks periodically via Chrome.
- We can also configure on‑failure behavior, like invoking an agent.
- We'll create a DFL in the Makefile that defines .names and maps error codes to actions, e.g., 1 = invoke the agent, 2 = fucking failed but don’t invoke the agent.
- All of this will be expressed as JSON configuration (e.g., periodic_tomo.json) that mentions my tasks and their settings.
- Everything is defined in code, making it high‑leverage and easy to manage.

Planning to extend Agenticd with Chrome periodic scheduler support, dedicated data structures, UI, and flows.

- Configure Chrome to run periodic tasks defined in Agenticd.
- Define tasks and schedules in a new config file (e.g., `periodic_tomo.json`).
- Add a DFL in the Makefile that defines `.names` and maps error codes to actions (e.g., `1` = invoke the agent, `2` = fucking failed, don’t invoke the agent).
- Use error codes to decide whether to invoke an agent after a task runs.
- All configurations are code‑defined, keeping everything version‑controlled and high‑leverage.

## Transcript

So in relation to that we want to add to agenticd support for Chrome periodic schedulers for tasks, full dedicated data structures and UI and flows. So we can just configure it with Chrome, running periodic tests or any tasks, so basically my tasks. So we create my tasks and we can run them periodically. We configure it and it's going to run it and we can basically also configure like onFailure invoke this So then what we want to do, like we basically create some DFL where the makefile is going to define the .names, so it just invokes the .names Then basically through error code we can communicate if we want to invoke an agent or not So basically like for example maybe 1 is invoke the agent 2 is fucking failed but don't invoke the agent And so we just define this and what agent to invoke. All in code defined as configurations. So we can create a new like in the config like periodic tomo, JSON, define it all there, mention the my tasks and the configs. And so here it is really high leverage.
This may be included here instead
