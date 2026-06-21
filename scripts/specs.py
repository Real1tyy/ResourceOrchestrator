#!/usr/bin/env python3
"""Spec backlog viewer & planner.

Scans docs/specs/, reads each spec's lifecycle status plus its WSJF estimates
(`value`, `difficulty`) and `depends_on` prerequisites, and tells you what to work
on next — accounting for dependencies.

A spec is *ready* only when every spec in its `depends_on` is already `accepted`.
Anything still proposed whose prerequisites aren't met is *blocked* and is never
recommended. Ready specs are ranked by WSJF (value ÷ difficulty) — highest
value-per-unit-effort first (ADR 0016).

Usage:
    scripts/specs.py                 # planner: ready-to-start (ranked) + blocked + dep tree
    scripts/specs.py --all           # every spec, grouped by status, with WSJF
    scripts/specs.py --status accepted
    scripts/specs.py --no-color      # plain text (also auto-disables when piped)

Stdlib only — no dependencies, no install step.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Depth-independent shared-lib import (spec-scripts-shared-lib): walk up to the
# scripts root (the dir holding lib/) so this resolves whether the script sits at
# scripts/foo.py or scripts/<sub>/foo.py.
for _ancestor in Path(__file__).resolve().parents:
    if (_ancestor / "lib" / "__init__.py").is_file():
        sys.path.insert(0, str(_ancestor))
        break
import lib  # noqa: E402
from lib import frontmatter as _fm  # noqa: E402

# Resolved via git (worktree-correct, depth-independent) rather than a fixed-depth file-relative walk.
REPO_ROOT = lib.repo_root()
SPECS_DIR = REPO_ROOT / "docs" / "specs"

# Glyph + colour + blurb per status.
STATUS_STYLE = {
    "proposed": ("○", "yellow", "still to do"),
    "draft": ("·", "dim", "not ready"),
    "accepted": ("●", "green", "shipped"),
    "superseded": ("↻", "magenta", "replaced"),
    "archived": ("✕", "dim", "won't do / deferred"),
}
STATUS_ORDER = ["proposed", "draft", "accepted", "superseded", "archived"]
DONE_STATUSES = {"accepted"}  # what counts as "prerequisite satisfied"

# Valid planning-poker deck (ADR 0016). Off-deck values are flagged.
DECK = {0.5, 1, 2, 3, 5, 8, 13}


# --- model -------------------------------------------------------------------


@dataclass
class Spec:
    path: Path
    number: str
    title: str
    status: str = "unknown"
    spec_id: str = ""
    owner: str = ""
    created: str = ""
    updated: str = ""
    value: float | None = None
    difficulty: float | None = None
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(REPO_ROOT))

    @property
    def wsjf(self) -> float | None:
        if self.value is None or not self.difficulty:
            return None
        return self.value / self.difficulty


# --- frontmatter parsing -----------------------------------------------------
# Reused from the shared lib (spec-scripts-shared-lib) — one parser, shared with docs.py,
# instead of a per-script copy. The aliases keep the call sites below unchanged.

_frontmatter = _fm.block
_scalar = _fm.scalar
_number = _fm.number
_list_field = _fm.list_field


def load_specs() -> list[Spec]:
    specs: list[Spec] = []
    # Filenames are YYYY-MM-DD-HHMMSS-slug.md (spec 0063): glob all, skip the
    # `_template` scaffold. Sorted glob == chronological (the timestamp prefix).
    for path in sorted(SPECS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = _frontmatter(text)
        title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        # The timestamp prefix is the chronological ordering key (was the legacy
        # NNNN number); kept only as a deterministic tie-breaker in ranking.
        ts_m = re.match(r"(\d{4}-\d{2}-\d{2}-\d{6})", path.name)
        specs.append(
            Spec(
                path=path,
                number=ts_m.group(1) if ts_m else path.name,
                title=title_m.group(1).strip() if title_m else path.stem,
                status=_scalar(fm, "status") or "unknown",
                spec_id=_scalar(fm, "id"),
                owner=_scalar(fm, "owner"),
                created=_scalar(fm, "created"),
                updated=_scalar(fm, "updated"),
                value=_number(fm, "value"),
                difficulty=_number(fm, "difficulty"),
                tags=_list_field(fm, "tags"),
                depends_on=_list_field(fm, "depends_on"),
            )
        )
    return specs


# --- dependency logic --------------------------------------------------------


@dataclass
class DepStatus:
    dep_id: str
    target: Spec | None
    satisfied: bool


def resolve_deps(spec: Spec, by_id: dict[str, Spec]) -> list[DepStatus]:
    out: list[DepStatus] = []
    for dep_id in spec.depends_on:
        target = by_id.get(dep_id)
        satisfied = target is not None and target.status in DONE_STATUSES
        out.append(DepStatus(dep_id, target, satisfied))
    return out


def is_ready(spec: Spec, by_id: dict[str, Spec]) -> bool:
    """All prerequisites accepted (or no prerequisites)."""
    return all(d.satisfied for d in resolve_deps(spec, by_id))


def blockers(spec: Spec, by_id: dict[str, Spec]) -> list[DepStatus]:
    return [d for d in resolve_deps(spec, by_id) if not d.satisfied]


# --- presentation ------------------------------------------------------------

ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
}


class Paint:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        codes = "".join(ANSI.get(s, "") for s in styles)
        return f"{codes}{text}{ANSI['reset']}" if codes else text


def wsjf_str(spec: Spec) -> str:
    w = spec.wsjf
    if w is None:
        return "wsjf   —"
    return f"wsjf {w:>4.2f}"


def est_str(spec: Spec, paint: Paint) -> str:
    def f(n: float | None) -> str:
        if n is None:
            return "?"
        s = str(int(n)) if n == int(n) else str(n)
        return s if (n in DECK) else f"{s}!"  # ! flags an off-deck value

    return paint(f"[v {f(spec.value)} / d {f(spec.difficulty)} · {wsjf_str(spec)}]", "dim")


def status_bar(counts: dict[str, int], total: int, paint: Paint, width: int = 34) -> str:
    if total == 0:
        return ""
    segs = []
    for status in STATUS_ORDER:
        n = counts.get(status, 0)
        if n:
            _, color, _ = STATUS_STYLE[status]
            segs.append(paint("█" * max(1, round(width * n / total)), color))
    return "".join(segs)


def print_header(specs: list[Spec], paint: Paint) -> None:
    counts: dict[str, int] = {}
    for s in specs:
        counts[s.status] = counts.get(s.status, 0) + 1
    total = len(specs)
    print()
    print(paint(f"  Spec backlog — {total} specs in docs/specs/", "bold"))
    print(f"  {status_bar(counts, total, paint)}")
    legend = "   ".join(
        f"{paint(STATUS_STYLE[s][0], STATUS_STYLE[s][1])} {s} {counts.get(s, 0)}"
        for s in STATUS_ORDER
        if counts.get(s, 0)
    )
    print(f"  {legend}")
    print()


def print_spec_block(spec: Spec, paint: Paint, deps: list[DepStatus] | None = None) -> None:
    glyph, color, _ = STATUS_STYLE.get(spec.status, ("?", "cyan", ""))
    print(f"  {paint(glyph, color)} {paint(spec.title, 'bold')}   {est_str(spec, paint)}")
    meta = []
    if spec.owner:
        meta.append(f"owner {spec.owner}")
    if spec.updated:
        meta.append(f"updated {spec.updated}")
    if meta:
        print(f"    {paint(' · '.join(meta), 'dim')}")
    if deps:
        for d in deps:
            if d.satisfied:
                mark, c = "✓", "green"
                label = d.target.title if d.target else d.dep_id
            else:
                mark, c = "✗", "red"
                if d.target is None:
                    label = f"{d.dep_id} (missing)"
                else:
                    label = f"{d.target.title} [{d.target.status}]"
            print(f"    {paint(mark, c)} needs {paint(label, 'dim')}")
    print(f"    {paint(spec.rel, 'cyan')}")
    print()


def rank(specs: list[Spec]) -> list[Spec]:
    """WSJF desc; tie-break by lower difficulty, then authoring timestamp."""
    return sorted(
        specs,
        key=lambda s: (
            -(s.wsjf if s.wsjf is not None else -1),
            s.difficulty if s.difficulty is not None else 1e9,
            s.number,
        ),
    )


# --- views -------------------------------------------------------------------


def view_planner(specs: list[Spec], paint: Paint) -> None:
    by_id = {s.spec_id: s for s in specs if s.spec_id}
    proposed = [s for s in specs if s.status == "proposed"]
    ready = [s for s in proposed if is_ready(s, by_id)]
    blocked = [s for s in proposed if not is_ready(s, by_id)]

    print_header(specs, paint)

    # --- Recommended next ----------------------------------------------------
    print(paint("▶ READY TO START", "green", "bold"), paint("— prerequisites met, ranked by WSJF", "dim"))
    print(paint("─" * 52, "dim"))
    print()
    if not ready:
        print(paint("  Nothing actionable — every proposed spec is blocked (see below).", "yellow"))
        print()
    else:
        for i, spec in enumerate(rank(ready), 1):
            tag = paint("  ★ DO THIS NEXT", "green", "bold") if i == 1 else ""
            print(f"  {paint(f'{i}.', 'bold')} {paint(spec.title, 'bold')}   {est_str(spec, paint)}{tag}")
            print(f"     {paint(spec.rel, 'cyan')}")
        print()

    # --- Blocked -------------------------------------------------------------
    if blocked:
        print(paint("⛔ BLOCKED", "red", "bold"), paint("— waiting on unfinished prerequisites", "dim"))
        print(paint("─" * 52, "dim"))
        print()
        for spec in rank(blocked):
            print_spec_block(spec, paint, deps=resolve_deps(spec, by_id))

    # --- Dependency tree -----------------------------------------------------
    print(paint("🌳 DEPENDENCY TREE", "blue", "bold"), paint("— proposed work, branches = dependents", "dim"))
    print(paint("─" * 52, "dim"))
    print()
    print_tree(proposed, by_id, paint)
    print()
    print(paint("  Tip: scripts/specs.py --all  for the full board (every status).", "dim"))
    print()


def print_tree(proposed: list[Spec], by_id: dict[str, Spec], paint: Paint) -> None:
    """Forest of proposed specs: roots are ready; children depend on their parent."""
    prop_ids = {s.spec_id for s in proposed}
    # dependents[p] = proposed specs that list p in their depends_on
    dependents: dict[str, list[Spec]] = {s.spec_id: [] for s in proposed}
    has_proposed_parent: set[str] = set()
    for s in proposed:
        for dep_id in s.depends_on:
            if dep_id in prop_ids:
                dependents[dep_id].append(s)
                has_proposed_parent.add(s.spec_id)

    roots = [s for s in proposed if s.spec_id not in has_proposed_parent]
    printed: set[str] = set()

    def render(spec: Spec, prefix: str, last: bool) -> None:
        ready = is_ready(spec, by_id)
        connector = "└─ " if last else "├─ "
        glyph, color = ("○", "green") if ready else ("○", "red")
        state = paint("READY", "green") if ready else paint("blocked", "red")
        seen = spec.spec_id in printed
        suffix = paint(" …(shown above)", "dim") if seen else ""
        line = (
            f"  {prefix}{connector}{paint(glyph, color)} "
            f"{paint(spec.title, 'bold')}  "
            f"[{state}] {est_str(spec, paint)}{suffix}"
        )
        print(line)
        if seen:
            return
        printed.add(spec.spec_id)
        kids = rank(dependents.get(spec.spec_id, []))
        child_prefix = prefix + ("   " if last else "│  ")
        for i, kid in enumerate(kids):
            render(kid, child_prefix, i == len(kids) - 1)

    roots = rank(roots)
    for i, root in enumerate(roots):
        render(root, "", i == len(roots) - 1)


def view_grouped(specs: list[Spec], paint: Paint, only: str | None) -> None:
    by_id = {s.spec_id: s for s in specs if s.spec_id}
    print_header(specs, paint)
    statuses = STATUS_ORDER + sorted({s.status for s in specs} - set(STATUS_ORDER))
    if only:
        statuses = [only]
    for status in statuses:
        group = rank([s for s in specs if s.status == status])
        if not group:
            if only:
                print(paint(f"  Nothing at status: {only}", "yellow"))
            continue
        _, color, blurb = STATUS_STYLE.get(status, ("?", "cyan", ""))
        label = f"{status.upper()} ({len(group)})"
        tail = f"  — {blurb}" if blurb else ""
        print(paint(f"{label}{tail}", color, "bold"))
        print(paint("─" * (len(label) + len(tail)), "dim"))
        for spec in group:
            deps = resolve_deps(spec, by_id) if spec.depends_on else None
            print_spec_block(spec, paint, deps=deps)


# --- entry -------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Spec backlog viewer & dependency-aware planner.")
    ap.add_argument("--status", help="Show only specs with this status, grouped view.")
    ap.add_argument("--all", action="store_true", help="Show every spec, grouped by status.")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colour.")
    args = ap.parse_args()

    use_color = (
        not args.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    )
    paint = Paint(use_color)

    if not SPECS_DIR.is_dir():
        print(paint(f"No specs dir at {SPECS_DIR}", "red"), file=sys.stderr)
        return 1
    specs = load_specs()
    if not specs:
        print(paint("No specs found.", "yellow"))
        return 0

    if args.all or args.status:
        view_grouped(specs, paint, only=args.status)
    else:
        view_planner(specs, paint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
