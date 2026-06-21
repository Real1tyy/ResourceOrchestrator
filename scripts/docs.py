#!/usr/bin/env python3
"""docs.py — sanctioned authoring CLI for the structured-markdown docs corpus.

The repo keeps four doc directories — ``docs/{specs,decisions,memory,knowledge}/`` —
each with a YAML-frontmatter contract and a collision-free filename convention
([[decision-collision-free-doc-ordering]], DOCS.md §3–§7). Hand-writing a new node
("copy ``_template.md.tmpl`` by eye") drifts the frontmatter and, worse, collides on the
``YYYY-MM-DD-HHMMSS`` prefix when two non-coordinating worktrees reach for the same second
under the rebase model ([[decision-worktree-rebase-model]]).

This is the **creation primitive** that closes both gaps. ``new`` builds a contract-correct
node from a single JSON object: frontmatter defaults come from the type's template, a key
present in the JSON overrides that default, and the timestamp prefix is chosen by scanning
the type's directory across the **root checkout and every sibling worktree** — incrementing
by whole seconds until the prefix is free everywhere — then written with an exclusive
``open(..., "x")``. The agent edits the node directly afterward; this tool only *creates*.
``list`` / ``show`` / ``search`` are thin read-only conveniences.

Frontmatter parsing/rendering is reused from :mod:`lib.frontmatter` (shared with
``specs.py``), and repo/worktree resolution from :mod:`lib` — no duplication here.

Usage:
    docs.py new <type> --json '{"name": "...", "tags": [...], "body": "..."}'
    docs.py new spec --json -            # read the JSON object from stdin
    docs.py list <type> [--status S] [--json]
    docs.py show <id> [--json]
    docs.py search <query> [--json]

Stdlib only — no dependencies, no install step (runs on the commit path / bare python3).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

# Depth-independent shared-lib import (spec-scripts-shared-lib): walk up to the scripts root
# (the dir holding lib/) so this resolves whether the script sits at scripts/foo.py or
# scripts/<sub>/foo.py.
for _ancestor in Path(__file__).resolve().parents:
    if (_ancestor / "lib" / "__init__.py").is_file():
        sys.path.insert(0, str(_ancestor))
        break
import lib  # noqa: E402
from lib import frontmatter  # noqa: E402

# Per-type config. ``timestamped`` mirrors guard_doc_names.TIMESTAMPED_DIRS: those dirs
# carry the contended YYYY-MM-DD-HHMMSS ordering key, so their prefix must be globally
# unique; ``knowledge`` is slug-only (uncontended) and uniqueness is by slug.
DOC_TYPES = {
    "spec": {"dir": "specs", "timestamped": True},
    "decision": {"dir": "decisions", "timestamped": True},
    "memory": {"dir": "memory", "timestamped": True},
    "knowledge": {"dir": "knowledge", "timestamped": False},
}

# Frontmatter keys the tool computes — never overridable from JSON (the identity + date
# contract). ``id`` is always ``<type>-<slug>``; ``type`` is fixed by the subcommand.
RESERVED_KEYS = ("type", "id", "created", "updated")

_PREFIX_FMT = "%Y-%m-%d-%H%M%S"
# Fixed-width glob for a YYYY-MM-DD-HHMMSS-prefixed node: each `?` is exactly one char, so
# the 17-char timestamp can't over-match into a longer slug (a plain `*-<slug>` would).
_TS_GLOB = "????-??-??-??????"
# Compiled once (not per call / per file): slug normalization + the H1-title reader.
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


class DocsError(Exception):
    """A user-facing failure: caught at the entry point and printed without a traceback."""


# --- slug + body-template parsing (authoring-specific) -----------------------


def slugify(name: str) -> str:
    """``"Watch-main  Rebase!"`` → ``"watch-main-rebase"``. Lower-case; non-``[a-z0-9]`` runs
    collapse to a single ``-``; ends trimmed. Raises on an all-punctuation ``name``."""
    slug = _SLUG_NON_ALNUM.sub("-", name.lower()).strip("-")
    if not slug:
        raise DocsError(f"name {name!r} slugifies to empty — give it letters/digits")
    return slug


def parse_body_template(body_text: str) -> tuple[str, str]:
    """``(heading_label, skeleton)`` from a template body whose heading still carries the
    ``{{title}}`` placeholder. The label is the word in ``# <Label>: {{title}}``; the
    skeleton is everything after that heading (so the leading instruction comment is
    dropped). A template with nothing after the heading yields an empty skeleton."""
    lines = body_text.splitlines()
    label, idx = "", -1
    for k, line in enumerate(lines):
        m = re.match(r"^#\s+(.*?):\s*\{\{title\}\}\s*$", line)
        if m:
            label, idx = m.group(1).strip(), k
            break
    skeleton = "\n".join(lines[idx + 1 :]).strip("\n") if idx != -1 else ""
    return label, skeleton


def render_node(
    type_name: str,
    slug: str,
    title: str,
    template_text: str,
    overrides: dict,
    body: str | None,
    today: str,
) -> str:
    """Assemble a full node from the type's template + the JSON overrides/body.

    ``{{slug}}``/``{{date}}`` are substituted before the frontmatter is parsed; ``{{title}}``
    is left in place so the heading label can be read, then the heading is rebuilt from the
    label + the real title (substituting ``{{title}}`` directly would corrupt label parsing)."""
    prepared = frontmatter.strip_managed_banner(template_text)
    prepared = prepared.replace("{{slug}}", slug).replace("{{date}}", today)
    fm_text, body_text = frontmatter.split(prepared)
    fields = frontmatter.parse_fields(fm_text)
    label, skeleton = parse_body_template(body_text)
    label = label or type_name.capitalize()

    computed = {
        "type": type_name,
        "id": f"{type_name}-{slug}",
        "created": today,
        "updated": today,
    }
    fm_block = frontmatter.render_fields(fields, overrides, computed)
    heading = f"# {label}: {title}"
    content = body if (body is not None and body.strip()) else skeleton
    parts = [f"---\n{fm_block}\n---", "", heading]
    if content and content.strip():
        parts += ["", content.strip("\n")]
    return "\n".join(parts).rstrip() + "\n"


# --- cross-worktree uniqueness -----------------------------------------------


def doc_scan_dirs(type_dir: str, root: Path) -> list[Path]:
    """Every ``docs/<type_dir>/`` to scan for collisions: the root checkout plus each
    ``<root>/.claude/worktrees/*/``. ``root`` is the **main** checkout (resolved even from
    inside a worktree), so the scan sees sibling worktrees' in-flight nodes."""
    dirs = [root / "docs" / type_dir]
    wt_base = root / ".claude" / "worktrees"
    if wt_base.is_dir():
        dirs.extend(wt / "docs" / type_dir for wt in sorted(wt_base.iterdir()) if wt.is_dir())
    return dirs


def _dir_has(d: Path, pattern: str) -> bool:
    """Whether any name in ``d`` matches ``pattern`` — short-circuits on the first hit, no
    aggregation. A missing dir is tolerated."""
    return d.is_dir() and next(d.glob(pattern), None) is not None


def prefix_taken(dirs: list[Path], prefix: str) -> bool:
    """Whether any node already uses this ``YYYY-MM-DD-HHMMSS`` prefix across ``dirs``.

    A targeted glob per dir, NOT a full-tree scan + set-aggregate: picking ``now`` and
    testing that single prefix is almost always one free lookup (a same-second cross-worktree
    collision is vanishingly rare), so there is no point regex-matching and set-building every
    filename in the corpus just to test one membership."""
    return any(_dir_has(d, f"{prefix}-*.md") for d in dirs)


def slug_taken(dirs: list[Path], slug: str, timestamped: bool) -> bool:
    """Whether the id ``<type>-<slug>`` already exists across ``dirs`` (any prefix). The
    fixed-width ``_TS_GLOB`` stops ``*-<slug>`` from over-matching a longer slug; knowledge
    (slug-only) is an exact-name ``exists()``."""
    if timestamped:
        return any(_dir_has(d, f"{_TS_GLOB}-{slug}.md") for d in dirs)
    return any((d / f"{slug}.md").exists() for d in dirs)


def pick_prefix(start: str, is_taken) -> str:
    """The first free ``YYYY-MM-DD-HHMMSS`` at/after ``start``, incrementing by whole
    seconds. ``is_taken(prefix) -> bool`` is the existence probe (the live filesystem, or a
    set in tests) — so the search stays "pick now, test it, bump on the rare hit"."""
    dt = _dt.datetime.strptime(start, _PREFIX_FMT)
    while is_taken(dt.strftime(_PREFIX_FMT)):
        dt += _dt.timedelta(seconds=1)
    return dt.strftime(_PREFIX_FMT)


# --- create ------------------------------------------------------------------


def _load_json_arg(raw: str | None) -> dict:
    """Parse the ``--json`` payload from the arg, or stdin when it is ``-``/omitted."""
    if raw is None or raw == "-":
        raw = sys.stdin.read()
    raw = (raw or "").strip()
    if not raw:
        raise DocsError("no JSON provided — pass --json '{...}' or pipe it on stdin")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DocsError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise DocsError('JSON payload must be an object, e.g. {"name": "…"}')
    return obj


def _read_template(root: Path, type_dir: str) -> str:
    path = root / "docs" / type_dir / "_template.md.tmpl"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocsError(f"missing template {path} — run `agenticd init`?") from exc


def create_node(type_name: str, payload: dict, *, now: _dt.datetime) -> dict:
    """Create one node and return ``{path, id, slug, timestamp}``. The file lands in the
    **current** checkout's ``docs/<type>/`` (the caller's worktree); the cross-worktree
    scan governs only prefix/slug selection."""
    if type_name not in DOC_TYPES:
        raise DocsError(f"unknown type {type_name!r} — one of: {', '.join(sorted(DOC_TYPES))}")
    cfg = DOC_TYPES[type_name]
    payload = dict(payload)

    name = payload.pop("name", None)
    if not name or not str(name).strip():
        raise DocsError("'name' is required (it becomes the slug and id)")
    title = str(payload.pop("title", "") or "").strip() or str(name).strip()
    body = payload.pop("body", None)
    if body is not None and not isinstance(body, str):
        raise DocsError("'body' must be a string")

    overrides = payload  # everything else is a frontmatter override/addition
    bad = sorted(k for k in overrides if k in RESERVED_KEYS)
    if bad:
        raise DocsError(
            f"these keys are computed and cannot be set: {', '.join(bad)} "
            f"(id is always {type_name}-<slug>; created/updated are today)"
        )

    slug = slugify(str(name))
    write_root = lib.repo_root()
    scan_root = lib.checkout_root(write_root)
    dirs = doc_scan_dirs(cfg["dir"], scan_root)

    if slug_taken(dirs, slug, cfg["timestamped"]):
        raise DocsError(
            f"id '{type_name}-{slug}' already exists across the repo + worktrees — "
            f"choose a distinct name"
        )

    write_dir = write_root / "docs" / cfg["dir"]
    write_dir.mkdir(parents=True, exist_ok=True)
    today = now.date().isoformat()
    text = render_node(type_name, slug, title, _read_template(write_root, cfg["dir"]), overrides, body, today)

    if cfg["timestamped"]:
        def _taken(prefix: str) -> bool:
            return prefix_taken(dirs, prefix)

        start = now.strftime(_PREFIX_FMT)
        while True:
            prefix = pick_prefix(start, _taken)
            target = write_dir / f"{prefix}-{slug}.md"
            try:
                with open(target, "x", encoding="utf-8") as fh:
                    fh.write(text)
                break
            except FileExistsError:
                # Lost the race for this second — resume the search just past it.
                start = (
                    _dt.datetime.strptime(prefix, _PREFIX_FMT) + _dt.timedelta(seconds=1)
                ).strftime(_PREFIX_FMT)
        timestamp = prefix
    else:
        target = write_dir / f"{slug}.md"
        try:
            with open(target, "x", encoding="utf-8") as fh:
                fh.write(text)
        except FileExistsError as exc:
            raise DocsError(f"{target.relative_to(write_root)} already exists") from exc
        timestamp = ""

    return {
        "path": str(target.relative_to(write_root)),
        "id": f"{type_name}-{slug}",
        "slug": slug,
        "timestamp": timestamp,
    }


# --- read-only conveniences --------------------------------------------------


def _read_doc(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fm_text, body = frontmatter.split(text)
    title_m = _H1_RE.search(body)
    return {
        "id": frontmatter.scalar(fm_text, "id"),
        "status": frontmatter.scalar(fm_text, "status"),
        "tags": frontmatter.list_field(fm_text, "tags"),
        "title": title_m.group(1).strip() if title_m else path.stem,
        "path": str(path),
        "body": body,
    }


def _iter_docs(root: Path, type_dir: str | None = None):
    base = root / "docs"
    dirs = [base / type_dir] if type_dir else [base / c["dir"] for c in DOC_TYPES.values()]
    for d in dirs:
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                if not p.name.startswith("_"):
                    yield p


def cmd_list(type_name: str, status: str | None, as_json: bool) -> int:
    if type_name not in DOC_TYPES:
        raise DocsError(f"unknown type {type_name!r} — one of: {', '.join(sorted(DOC_TYPES))}")
    root = lib.repo_root()
    rows = [_read_doc(p) for p in _iter_docs(root, DOC_TYPES[type_name]["dir"])]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if as_json:
        print(json.dumps([{k: r[k] for k in ("id", "status", "title", "path")} for r in rows]))
    else:
        for r in rows:
            print(f"{r['status']:>10}  {r['id']:<48}  {r['title']}")
    return 0


def cmd_show(doc_id: str, as_json: bool) -> int:
    root = lib.repo_root()
    for p in _iter_docs(root):
        doc = _read_doc(p)
        if doc["id"] == doc_id:
            if as_json:
                print(json.dumps(doc))
            else:
                print(p.read_text(encoding="utf-8"), end="")
            return 0
    raise DocsError(f"no doc with id '{doc_id}'")


def cmd_search(query: str, as_json: bool) -> int:
    root = lib.repo_root()
    q = query.lower()
    hits = []
    for p in _iter_docs(root):
        doc = _read_doc(p)
        haystack = f"{doc['title']}\n{' '.join(doc['tags'])}\n{doc['body']}".lower()
        if q in haystack:
            line = next(
                (ln for ln in doc["body"].splitlines() if q in ln.lower()), doc["title"]
            )
            hits.append({"id": doc["id"], "path": doc["path"], "match": line.strip()})
    if as_json:
        print(json.dumps(hits))
    else:
        for h in hits:
            print(f"{h['id']:<48}  {h['match']}")
    return 0


# --- entry -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Author + query the structured docs corpus.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="create a doc node from a JSON object")
    p_new.add_argument("type", help="one of: " + ", ".join(sorted(DOC_TYPES)))
    p_new.add_argument("--json", dest="json_arg", default=None, help="JSON object ('-' = stdin)")

    p_list = sub.add_parser("list", help="list nodes of a type")
    p_list.add_argument("type")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--json", dest="as_json", action="store_true")

    p_show = sub.add_parser("show", help="print a node by canonical id")
    p_show.add_argument("id")
    p_show.add_argument("--json", dest="as_json", action="store_true")

    p_search = sub.add_parser("search", help="substring search over title/tags/body")
    p_search.add_argument("query")
    p_search.add_argument("--json", dest="as_json", action="store_true")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "new":
            payload = _load_json_arg(args.json_arg)
            print(json.dumps(create_node(args.type, payload, now=_dt.datetime.now())))
            return 0
        if args.cmd == "list":
            return cmd_list(args.type, args.status, args.as_json)
        if args.cmd == "show":
            return cmd_show(args.id, args.as_json)
        if args.cmd == "search":
            return cmd_search(args.query, args.as_json)
    except DocsError as exc:
        print(f"docs: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
