"""scripts/lib/frontmatter — shared YAML-frontmatter helpers for the repo scripts.

One home for the frontmatter handling that was otherwise copy-pasted across ``scripts/``
(``specs.py`` had its own ``_frontmatter``/``_scalar``/``_list_field``; ``guard_doc_names``
its own id reader). Two access styles, both stdlib-only:

* **per-key readers** — :func:`scalar` / :func:`number` / :func:`list_field` / :func:`doc_id`,
  for a script that just wants one field (the backlog viewer, the name guard). These mirror
  the tolerant ``agenticd-docs`` frontmatter contract (DOCS.md §3).
* **ordered parse + render** — :func:`parse_fields` / :func:`render_fields`, the authoring
  path (``docs.py``): parse a template's frontmatter into an ordered field list, merge JSON
  overrides over the defaults, and re-emit a clean block.

Kept stdlib-only (no PyYAML): these run on the commit path and inside ``uv``-launched
scripts, so they must import with no dependency resolution.
"""

from __future__ import annotations

import re


def split(text: str) -> tuple[str, str]:
    """``(frontmatter_text, body_text)`` for a ``---``-fenced doc; ``("", text)`` when the
    first line is not exactly ``---``. An unterminated fence yields all-following-text as
    the frontmatter and an empty body (matching the legacy ``specs._frontmatter`` behavior)."""
    if not text.startswith("---"):
        return "", text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return "", text
    fm: list[str] = []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(fm), "\n".join(lines[i + 1 :])
        fm.append(lines[i])
    return "\n".join(fm), ""


def block(text: str) -> str:
    """Just the frontmatter block of ``text`` (``split(text)[0]``)."""
    return split(text)[0]


def scalar(fm: str, key: str) -> str:
    """A scalar field's value from a frontmatter block, trailing ``# comment`` stripped.
    Empty string when absent."""
    m = re.search(rf"^{key}:\s*(.+?)\s*(?:#.*)?$", fm, re.MULTILINE)
    return m.group(1).strip().strip("\"'") if m else ""


def number(fm: str, key: str) -> float | None:
    """A numeric field, or ``None`` when absent / non-numeric."""
    raw = scalar(fm, key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def list_field(fm: str, key: str) -> list[str]:
    """A list field — inline (``key: [a, b]``) or YAML block (``- a`` lines). ``[]`` / absent
    yields ``[]``."""
    inline = re.search(rf"^{key}:\s*\[(.*?)\]\s*$", fm, re.MULTILINE)
    if inline:
        return [x.strip().strip("\"'") for x in inline.group(1).split(",") if x.strip()]
    blk = re.search(rf"^{key}:\s*$((?:\n[ \t]+-.*)*)", fm, re.MULTILINE)
    if blk:
        return [
            ln.strip()[1:].strip().strip("\"'")
            for ln in blk.group(1).splitlines()
            if ln.strip().startswith("-")
        ]
    return []


def strip_managed_banner(text: str) -> str:
    """Drop a leading ``<!-- … -->`` HTML comment (the ``agenticd:managed-doc`` banner, or a
    template's instruction comment) plus surrounding whitespace, so parsing/emission starts
    at the ``---`` fence or the heading."""
    stripped = text.lstrip()
    if stripped.startswith("<!--"):
        end = stripped.find("-->")
        if end != -1:
            return stripped[end + 3 :].lstrip()
    return stripped


# --- ordered parse + render (the authoring path) -----------------------------


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ``# …`` comment preceded by whitespace (the template's
    ``value: 3        # deck …`` style); a ``#`` with no leading space (a URL fragment)
    survives."""
    return re.sub(r"\s+#.*$", "", value)


def parse_fields(fm: str) -> list[tuple[str, str, object]]:
    """Parse a flat frontmatter block into an ordered ``[(key, kind, value)]`` list.

    ``kind`` is ``"scalar"`` (a ``str``) or ``"list"`` (a ``list[str]``). ``key: []`` is an
    empty list; a bare ``key:`` then indented ``- item`` lines is a block list; a bare
    ``key:`` with no items is an empty scalar. Order is preserved so emission matches the
    template's field order."""
    lines = fm.splitlines()
    fields: list[tuple[str, str, object]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), _strip_inline_comment(m.group(2)).strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
            fields.append((key, "list", items))
            i += 1
            continue
        if rest == "":
            items, j = [], i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    j += 1
                    continue
                if re.match(r"^\s+-\s+", nxt):
                    items.append(_strip_inline_comment(nxt.strip()[1:]).strip().strip("\"'"))
                    j += 1
                else:
                    break
            if items:
                fields.append((key, "list", items))
                i = j
            else:
                fields.append((key, "scalar", ""))
                i += 1
            continue
        fields.append((key, "scalar", rest.strip("\"'")))
        i += 1
    return fields


def _render_scalar(key: str, value: object) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, float) and value.is_integer():
        value = str(int(value))
    text = "" if value is None else str(value)
    return f"{key}:" if text == "" else f"{key}: {text}"


def _render_list(key: str, items: list) -> str:
    if not items:
        return f"{key}: []"
    return f"{key}:\n" + "\n".join(f"  - {it}" for it in items)


def _render_field(key: str, value: object, template_kind: str | None) -> str:
    """Render one line. A ``list`` value is always a block/``[]`` list; otherwise the
    template's kind decides (so an untouched ``tags: []`` stays a list), defaulting to
    scalar for keys with no template entry."""
    if isinstance(value, list):
        return _render_list(key, value)
    if template_kind == "list" and value in (None, ""):
        return _render_list(key, [])
    return _render_scalar(key, value)


def render_fields(
    fields: list[tuple[str, str, object]],
    overrides: dict,
    computed: dict,
) -> str:
    """Render frontmatter: template fields in order (computed/overridden in place), then
    any override-only keys appended in insertion order. ``computed`` (id/type/dates) wins
    over an override; the caller rejects reserved overrides before calling."""
    out: list[str] = []
    seen: set[str] = set()
    template_kind = {k: kind for k, kind, _ in fields}
    for key, kind, default in fields:
        seen.add(key)
        if key in computed:
            out.append(_render_field(key, computed[key], kind))
        elif key in overrides:
            out.append(_render_field(key, overrides[key], kind))
        else:
            out.append(_render_field(key, default, kind))
    for key, value in overrides.items():
        if key not in seen:
            out.append(_render_field(key, value, template_kind.get(key)))
    return "\n".join(out)
