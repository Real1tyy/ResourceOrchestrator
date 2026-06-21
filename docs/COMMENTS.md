# COMMENTS.md — Comment hygiene

> Seeded by `agenticd init` as a placeholder. This repo owns this file — edit it
> freely. Unlike the managed convention mirrors (`AGENTS.md`, the templates), init
> never overwrites this once it exists; downstream fills it in.

Comment the **why**, never the **what** or the **how**. Names carry *what*; types
and structure carry *how*. A comment earns its place only when it says something the
code cannot: a rationale, a trade-off, an invariant, a "looks wrong but is
deliberate", or a spec/ADR pointer.

- **Delete:** comments that restate a name; boilerplate that echoes a signature;
  prose that narrates the body line by line.
- **Keep:** the rationale for a non-obvious choice, ordering constraints, footgun
  warnings, module-purpose headers. When unsure, keep and trim.

Document this project's specific comment conventions below.
