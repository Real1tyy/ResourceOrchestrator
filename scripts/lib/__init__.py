"""scripts/lib — shared helpers for the repo scripts (spec-scripts-shared-lib).

Collapses four idioms that were copy-pasted across ``scripts/``:

* **depth-independent repo-root resolution** — :func:`repo_root` asks git
  (``git rev-parse --show-toplevel``) instead of a fixed-depth ``__file__``-relative
  walk, so it is worktree-correct *and* survives a script moving into a subfolder (the
  load-bearing property for [[spec-scripts-module-layout]]'s folder move);
* a thin :func:`run` subprocess wrapper;
* a scrubbed argv-only :func:`git` helper, consistent with
  [[decision-git-cli-first-defer-gitoxide]] (never a shell string);
* a fixture-safe :func:`scrubbed_git` / :func:`scrubbed_git_env` pair — the Python
  analogue of ``agenticd_git::scrubbed_git_command()`` — that strips git's
  repo-location vars so a throwaway-repo test can't inherit a hook's ``GIT_DIR`` and
  mutate the real repo ([[decision-isolate-git-subprocess-env]],
  [[spec-python-fixture-git-scrub-enforcement]]);
* a JSONL telemetry-emit helper (:func:`append_jsonl` / :func:`emit_telemetry`)
  that lands events under the **root** checkout's ``.agentic/`` tree, mirroring
  ``agentic_telemetry._resolve_checkout`` (telemetry never lands in a worktree's
  own ``.agentic/``).

Importing scripts reach this module through a **depth-independent shim** — they
walk up to the directory holding ``lib/`` and put it on ``sys.path`` — so the
import resolves identically whether the script sits at ``scripts/foo.py`` or
``scripts/<sub>/foo.py``::

    import sys
    from pathlib import Path

    for _ancestor in Path(__file__).resolve().parents:
        if (_ancestor / "lib" / "__init__.py").is_file():
            sys.path.insert(0, str(_ancestor))
            break
    import lib  # noqa: E402

Stdlib only — these helpers run on the commit path and inside ``uv``-launched
mirror scripts, so they must import with no dependency resolution.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# Worktrees live at ``<root>/.claude/worktrees/<name>`` — the segment we strip to
# get back to the main checkout. Kept identical to the marker in
# ``agentic_telemetry._resolve_checkout`` so the two never disagree on where a
# root-bound artifact (telemetry, gate logs) belongs.
WORKTREE_MARKER = "/.claude/worktrees/"


def run(argv, **kwargs):
    """Thin ``subprocess.run`` wrapper sharing the scripts' common defaults.

    ``text=True`` by default (the scripts uniformly work in strings, never bytes);
    everything else is the caller's via ``**kwargs`` (``capture_output``, ``check``,
    ``cwd``, ``env``, …). ``argv`` is coerced to a list so a tuple call site works.
    """
    kwargs.setdefault("text", True)
    return subprocess.run(list(argv), **kwargs)


def git(argv, cwd=None, check=True, capture=True):
    """Run a git command as a scrubbed **argv array**, never a shell string.

    ``argv`` is the git arguments *without* the leading ``git`` (e.g.
    ``["rev-parse", "HEAD"]``). A ``str`` ``argv`` is rejected outright: a shell
    string is exactly the foot-gun [[decision-git-cli-first-defer-gitoxide]]
    forbids. Returns the ``CompletedProcess`` (text mode); with ``check=True`` a
    non-zero exit raises ``CalledProcessError``.
    """
    if isinstance(argv, str):
        raise TypeError("git() takes an argv list, not a shell string")
    return subprocess.run(
        ["git", *argv],
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=check,
    )


# Git exports its repo location into a hook's environment and those vars OVERRIDE
# directory-based discovery — so a fixture's `git -C tmp …` run under the gate is
# silently retargeted at the REAL repo (once this destroyed a repo's .git; ADR 0032 /
# [[decision-isolate-git-subprocess-env]]). A throwaway-repo fixture MUST strip them.
# Identical to scripts/guards/guard_worktree_paths.py and gate_steps.GIT_LOCATION_VARS
# (one source of truth by convention — keep the three in sync).
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def scrubbed_git_env(env=None):
    """A copy of ``env`` (default ``os.environ``) safe for a throwaway-repo fixture:
    git's repo-location vars removed (so ``cwd`` discovery wins instead of a hook's
    leaked ``GIT_DIR``) and user/system git config neutralized
    (``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` → ``os.devnull``) so a developer's
    ``~/.gitconfig`` can't make a fixture flaky. The env half of the Python analogue
    of ``agenticd_git::scrubbed_git_command()``."""
    src = os.environ if env is None else env
    out = {k: v for k, v in src.items() if k not in GIT_LOCATION_VARS}
    out["GIT_CONFIG_GLOBAL"] = os.devnull
    out["GIT_CONFIG_SYSTEM"] = os.devnull
    return out


def scrubbed_git(argv, cwd=None, check=True, capture=True):
    """Run ``git <argv>`` under :func:`scrubbed_git_env` — the fixture-safe git runner.

    The Python analogue of ``agenticd_git::scrubbed_git_command()``: EVERY test
    fixture that shells out to git in a throwaway repo must use this (or
    :func:`scrubbed_git_env`), never a raw ``subprocess.run(["git", …])`` — under a
    hook the raw form inherits ``GIT_DIR`` and mutates the real repo. A raw call in
    ``scripts/tests/**`` is blocked by ``scripts/guards/guard_git_subprocess.py``.
    ``argv`` is the git args without leading ``git``; a ``str`` argv is rejected (the
    shell-string foot-gun [[decision-git-cli-first-defer-gitoxide]] forbids)."""
    if isinstance(argv, str):
        raise TypeError("scrubbed_git() takes an argv list, not a shell string")
    return subprocess.run(
        ["git", *argv],
        cwd=cwd,
        env=scrubbed_git_env(),
        text=True,
        capture_output=capture,
        check=check,
    )


def repo_root(start=None):
    """The git top-level directory, resolved via ``git rev-parse --show-toplevel``.

    Worktree-correct (a linked worktree is its own top-level) **and**
    depth-independent: unlike a fixed-depth ``__file__``-relative walk it encodes
    no assumption about how deep the calling script sits under ``scripts/``.
    ``start`` sets the directory the query runs from (defaults to the process cwd).
    Raises ``RuntimeError`` when not inside a git repository.
    """
    result = git(["rev-parse", "--show-toplevel"], cwd=start, check=False)
    top = result.stdout.strip()
    if result.returncode != 0 or not top:
        raise RuntimeError("repo_root(): not inside a git repository")
    return Path(top)


def checkout_root(path=None):
    """The **main** checkout root, even when ``path`` is inside a worktree.

    Worktrees live at ``<root>/.claude/worktrees/<name>``; root-bound artifacts
    (telemetry, gate logs) must land under the root's ``.agentic/`` where the
    daemon watches — never the worktree's. Strips at the worktree marker, mirroring
    ``agentic_telemetry._resolve_checkout``. ``path`` defaults to :func:`repo_root`.
    """
    p = Path(path) if path is not None else repo_root()
    s = str(p)
    idx = s.find(WORKTREE_MARKER)
    if idx >= 0:
        return Path(s[:idx])
    return p


def append_jsonl(path, record):
    """Append ``record`` as one JSON line to ``path`` (best-effort, atomic per line).

    Creates parent directories, then appends in text mode — which maps to
    ``O_APPEND`` on POSIX, keeping each line whole under concurrent writers. The
    one shared primitive the JSONL-writing scripts use instead of hand-rolling
    ``open(..., "a")``. Returns the ``Path`` written, or ``None`` on any
    ``OSError``: emitting an observability line must never break its caller.
    """
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return target
    except OSError:
        return None


def emit_telemetry(rel_path, record, root=None):
    """Append a telemetry event under ``<checkout_root>/.agentic/telemetry/<rel_path>``.

    The root-resolving wrapper over :func:`append_jsonl`: it routes the event to the
    **root** checkout (via :func:`checkout_root`) so telemetry written from inside a
    worktree still lands where the daemon ingests it. ``root`` overrides the
    resolution (defaults to the process's repo). Best-effort — returns the ``Path``
    written or ``None``.
    """
    try:
        base = checkout_root(root) / ".agentic" / "telemetry"
    except (OSError, RuntimeError):
        return None
    return append_jsonl(base / rel_path, record)
