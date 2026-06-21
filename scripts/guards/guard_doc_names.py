#!/usr/bin/env python3
"""guard_doc_names.py — enforce collision-free timestamp filenames for docs.

The repo runs the parallel-worktree rebase model (ADR 0013): non-coordinating
worktrees integrate only at rebase. A sequential ``NNNN`` filename prefix is a
*globally contended ordering key* — two worktrees both reach for the next free
number and collide (it already happened: two ``0009``/``0012`` decisions, two
each of ``0041``/``0042``/``0043``/``0059`` specs). This is the exact class
ADR 0030 fixed for migrations; spec 0063 applies the same lever to docs: derive
the prefix from each worktree's own clock instead of a shared counter.

This guard is the backstop. It asserts, over ``docs/{specs,decisions,memory}/``:

  * every filename matches ``YYYY-MM-DD-HHMMSS-slug.md`` (no legacy ``NNNN-slug``),
  * no two files share a timestamp prefix (the collision it exists to prevent),
  * frontmatter ``id``s are unique across the whole ``docs/`` tree (identity is
    the canonical ``<type>-<slug>``; a duplicate id is a real ambiguity),
  * cross-references (frontmatter ``related_docs`` + body ``[[wikilinks]]``) use a
    canonical id, not a dated/numeric filename stem — the same rule the agenticd-docs
    corpus test enforces, lifted here so a dated ref fails the fast gate instead of
    surfacing late in ``mise run pre-stop`` (spec-guard-dated-doc-refs).

Templates (``_*.tmpl`` / leading-underscore names) are exempt.

Runs in ``mise run pre-commit`` (the fast per-commit checks) so a colliding or
legacy doc name fails before it can land, not after a painful rebase. Pairs with
the PreToolUse hook (spec 0063 T5), which blocks the bad write even earlier.

Human escape hatch: ``AGENTIC_ALLOW_LEGACY_DOC_NAMES=1`` permits a one-off commit
(e.g. landing the migration itself before the tree is fully converted).

Stdlib only — no dependencies.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Depth-independent shared-lib import (spec-scripts-shared-lib): walk up to the
# scripts root (the dir holding lib/) so this resolves whether the script sits at
# scripts/foo.py or scripts/<sub>/foo.py.
for _ancestor in Path(__file__).resolve().parents:
    if (_ancestor / "lib" / "__init__.py").is_file():
        sys.path.insert(0, str(_ancestor))
        break
import lib  # noqa: E402

# Resolved via git (worktree-correct, depth-independent) rather than a fixed-depth file-relative walk.
REPO_ROOT = lib.repo_root()
DOCS_DIR = REPO_ROOT / "docs"

# The dirs whose filenames are the contended ordering key. knowledge/ is
# slug-only (not numbered, not contended), so it is exempt from the filename
# rule (spec 0063 non-goals).
TIMESTAMPED_DIRS = ("specs", "decisions", "memory")

# `YYYY-MM-DD-HHMMSS-slug.md` — the non-contended, clock-derived ordering key.
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[a-z0-9][a-z0-9-]*\.md$")

# The 17-char `YYYY-MM-DD-HHMMSS` prefix that must be globally unique.
PREFIX_LEN = len("YYYY-MM-DD-HHMMSS")

# `[[wikilink]]` body references. Together with frontmatter `related_docs`, these are
# the cross-references the agenticd-docs corpus scanner treats as id links — the set
# the dated-reference check below mirrors (spec-guard-dated-doc-refs).
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _is_template(name: str) -> bool:
    """Templates (`_template.md.tmpl`, any leading-underscore scaffold) are exempt."""
    return name.startswith("_") or name.endswith(".tmpl")


def _frontmatter_id(text: str) -> str | None:
    """The canonical `id:` from a doc's YAML frontmatter, or None if absent."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[:end] if end != -1 else text
    m = re.search(r"^id:\s*(.+?)\s*$", block, re.MULTILINE)
    return m.group(1).strip().strip("\"'") if m else None


def _looks_legacy_ref(target: str) -> bool:
    """True if a cross-reference target is a legacy ``NNNN-``/``YYYY-MM-DD-`` filename
    stem rather than a canonical id. Mirrors ``looks_legacy`` in
    ``crates/agenticd-docs/tests/real_docs.rs``: the last path segment, ``.md``
    stripped, is ≥5 chars with the first four ASCII digits and the fifth a ``-``."""
    name = target.rsplit("/", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    return len(name) >= 5 and name[:4].isdigit() and name[4] == "-"


def _related_docs(frontmatter: str) -> list[str]:
    """``related_docs`` targets from a frontmatter block — inline (``[a, b]``) or YAML
    block list (``- a`` lines). Empty (``related_docs: []`` / absent) yields nothing."""
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^related_docs:\s*(.*)$", line)
        if not m:
            continue
        inline = m.group(1).strip()
        if inline.startswith("["):
            inner = inline[1:-1] if inline.endswith("]") else inline[1:]
            return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        out: list[str] = []
        for follow in lines[i + 1:]:
            stripped = follow.strip()
            if stripped == "":
                continue
            if follow[:1] in (" ", "\t") and stripped.startswith("- "):
                out.append(stripped[2:].strip().strip("\"'"))
            else:
                break  # next top-level key ends the list
        return out
    return []


def _doc_refs(text: str) -> list[str]:
    """Cross-reference targets a doc declares: frontmatter ``related_docs`` entries +
    body ``[[wikilink]]`` targets. Plain markdown ``[text](path)`` links are excluded
    on purpose — the corpus scanner doesn't treat them as id links, so a retrospective
    ``[..](../memory/2026-…md)`` line stays legal."""
    frontmatter = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        frontmatter = text[:end] if end != -1 else text
    return _related_docs(frontmatter) + _WIKILINK_RE.findall(text)


def check_tree(docs_dir: Path) -> list[str]:
    """Return human-readable violations for the docs tree rooted at ``docs_dir``.

    Empty list == clean. Pure function of the filesystem so tests can point it at
    a fixture tree.
    """
    errors: list[str] = []
    prefixes: dict[str, str] = {}  # timestamp prefix -> first rel path seen

    for sub in TIMESTAMPED_DIRS:
        d = docs_dir / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            name = path.name
            if _is_template(name):
                continue
            rel = f"docs/{sub}/{name}"
            if not NAME_RE.match(name):
                errors.append(
                    f"{rel}: legacy/invalid name — must be YYYY-MM-DD-HHMMSS-slug.md"
                )
                continue
            prefix = name[:PREFIX_LEN]
            if prefix in prefixes:
                errors.append(
                    f"{rel}: timestamp prefix {prefix} collides with {prefixes[prefix]}"
                )
            else:
                prefixes[prefix] = rel

    # id uniqueness AND legacy-cross-reference detection span the *whole* docs tree
    # (both are cross-cutting). The dated/numeric-reference check mirrors the
    # agenticd-docs corpus test at commit time so a dated ref fails the fast gate
    # instead of surfacing late in `mise run pre-stop` (spec-guard-dated-doc-refs).
    ids: dict[str, str] = {}
    for path in sorted(docs_dir.rglob("*.md")):
        if _is_template(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(docs_dir.parent).as_posix()

        for ref in _doc_refs(text):
            if _looks_legacy_ref(ref):
                errors.append(
                    f"{rel}: cross-reference '{ref}' uses a dated/numeric filename — "
                    f"reference docs by their canonical id ([[<type>-<slug>]])"
                )

        doc_id = _frontmatter_id(text)
        if not doc_id:
            continue
        if doc_id in ids:
            errors.append(f"{rel}: frontmatter id '{doc_id}' duplicates {ids[doc_id]}")
        else:
            ids[doc_id] = rel

    return errors


def main() -> int:
    if os.environ.get("AGENTIC_ALLOW_LEGACY_DOC_NAMES") == "1":
        print(
            "guard-doc-names: AGENTIC_ALLOW_LEGACY_DOC_NAMES=1 — skipping the doc-name gate",
            file=sys.stderr,
        )
        return 0

    if not DOCS_DIR.is_dir():
        print(f"guard-doc-names: no docs/ dir at {DOCS_DIR}", file=sys.stderr)
        return 0

    errors = check_tree(DOCS_DIR)
    if not errors:
        return 0

    listing = "\n".join(f"      {e}" for e in errors)
    print(
        f"""guard-doc-names: BLOCKED — doc names + cross-references must be id-based (spec 0063).

  Violation(s):
{listing}

  Docs in docs/{{specs,decisions,memory}}/ use a clock-derived prefix so parallel
  worktrees never collide on a shared number (ADR 0013 rebase model; ADR 0030
  precedent). Name new docs YYYY-MM-DD-HHMMSS-slug.md and reference them by their
  canonical frontmatter id ([[<type>-<slug>]]), not by number.

  Human escape hatch (one-off): AGENTIC_ALLOW_LEGACY_DOC_NAMES=1 git commit ...""",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
