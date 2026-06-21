#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""PreToolUse hook — default-deny capability gate for agents (spec 0031).

Bash commands are evaluated against an ALLOWLIST (default-deny): whatever is not
explicitly permitted is blocked with an exit-2 message that names the allowed
alternative. The allowlist is data (.claude/hooks/policy/allowlist.toml); the
engine is .claude/hooks/validators/command_policy.py. This replaces the old
allow-by-default blocklist (which could never be complete).

File tools (Read/Edit/Write/MultiEdit) keep the targeted ``.env`` guard — they are
not subject to the Bash allowlist, since denying all file writes would stop the
agent editing code.

Fail mode (spec 0031): for Bash, an internal error fails CLOSED for mutating
commands and OPEN for read-only ones — a hook bug degrades focus, not safety,
without bricking inspection. Other tools keep the legacy fail-safe (exit 0).
"""

import json
import os
import re
import sys
import time
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOK_DIR))
sys.path.insert(0, str(HOOK_DIR / "validators"))

try:
    from _hook_error import fail_safe
except ImportError:
    def fail_safe(exc: BaseException, hook_name: str) -> None:  # noqa: ARG001
        sys.exit(0)

import command_policy  # noqa: E402  (after sys.path setup)

try:
    from _extension import invoke_extension
except ImportError:  # dispatch helper absent → repo extensions disabled (no-op)
    def invoke_extension(stem, phase, data):  # noqa: ARG001
        return

# Gitignore-semantics matcher for the generated-files guard (spec
# generated-files-manifest-and-edit-block). Resolved from a VENDORED copy under
# `_vendor/` (pathspec 0.12.1, pure-Python — see _vendor/pathspec/PROVENANCE.md),
# NOT a uv-resolved dependency: the inline metadata declares `dependencies = []` so
# `uv run --script` runs at interpreter speed instead of paying the ~44–50 ms
# per-call dependency-environment tax (spec-hook-dedep-pathspec). Optional so the
# module still imports if the vendored copy is somehow absent — a missing matcher
# makes the guard fail OPEN (no block), never crash the hook.
sys.path.insert(0, str(HOOK_DIR / "_vendor"))
try:
    import pathspec
except ImportError:
    pathspec = None

# Programs that are always safe to allow if the policy engine itself fails to
# load — the ultimate fail-open-read-only fallback (no policy object needed).
_FALLBACK_READ_ONLY = {"ls", "cat", "rg", "grep", "pwd", "echo", "find", "head", "tail", "wc"}
_FALLBACK_GIT_READ_ONLY = {"status", "log", "diff", "show", "rev-parse"}

# Tools that write files. Subject to the no-edit-on-main guard (spec 0031 R7).
_FILE_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

_MAIN_EDIT_BLOCKED = (
    "BLOCKED: no file edits on the main checkout (spec 0031). This path is in the "
    "main working tree, not a worktree under .claude/worktrees/. Under the worktree "
    "workflow you must edit inside your worktree: run `mise run wt:new <slug>` (or "
    "enter your existing worktree) and edit there. Human pre-approval for a "
    "main-tree edit: launch with AGENTIC_ALLOW_MAIN_EDIT=1."
)


def _main_tree_edit_blocked(tool_name, tool_input, cwd):
    """Block a file-write tool whose target is in the main checkout (R7)."""
    if tool_name not in _FILE_WRITE_TOOLS:
        return False
    if os.environ.get("AGENTIC_ALLOW_MAIN_EDIT") == "1":
        return False
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not command_policy.is_main_tree_edit(file_path, cwd):
        return False
    try:
        return command_policy.load_policy().block_main_tree_edits
    except Exception:
        return True  # config unreadable → keep the guard on (the file path is known)


_GENERATED_MANIFEST_REL = (".claude", "hooks", "policy", "generated")

_GENERATED_EDIT_BLOCKED = (
    "BLOCKED: this is a generated file (listed in .claude/hooks/policy/generated). "
    "Regenerate it via its generator (e.g. `mise run gen:openapi`), don't hand-edit — a "
    "hand edit drifts from the generator and the contract/entity gates only catch it "
    "later. Human override for a deliberate edit: AGENTIC_ALLOW_GENERATED_EDIT=1."
)


def _enclosing_generated_manifest(abs_path):
    """Walk up from ``abs_path`` to the first ancestor holding
    ``.claude/hooks/policy/generated``; return ``(checkout_root, manifest_text)`` or
    ``(None, None)``.

    This *is* the checkout-root resolution (R4): every tracked checkout — the main one
    and each ``.claude/worktrees/<name>/`` — carries its own committed manifest, so the
    nearest ancestor with one is exactly the root the target should match against. A
    missing manifest in every ancestor, or an unreadable one, yields ``(None, None)`` so
    the guard fails open (it only ever *adds* protection)."""
    cur = os.path.dirname(os.path.abspath(abs_path))
    while True:
        manifest = os.path.join(cur, *_GENERATED_MANIFEST_REL)
        if os.path.isfile(manifest):
            try:
                return cur, Path(manifest).read_text(encoding="utf-8")
            except OSError:
                return None, None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, None
        cur = parent


def _generated_file_edit_blocked(tool_name, tool_input, cwd):
    """Block a file-write tool whose target matches the enclosing checkout's
    ``.agentic/generated`` manifest (spec generated-files-manifest-and-edit-block).

    Gitignore semantics via ``pathspec``; matched relative to the checkout root that
    owns the manifest, so it works identically in the main checkout and inside a
    worktree. Fails OPEN (no block) on: a non-write tool, the human override env, no
    matcher available, no/unreadable manifest, or a path outside any manifest's root."""
    if tool_name not in _FILE_WRITE_TOOLS:
        return False
    if os.environ.get("AGENTIC_ALLOW_GENERATED_EDIT") == "1":
        return False
    if pathspec is None:  # matcher unavailable (R6) → fail open
        return False
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return False
    fp = str(file_path).replace("\\", "/")
    if not os.path.isabs(fp):
        fp = os.path.normpath(os.path.join(str(cwd or ""), fp))
    root, manifest_text = _enclosing_generated_manifest(fp)
    if root is None or manifest_text is None:
        return False
    rel = os.path.relpath(fp, root).replace("\\", "/")
    if rel.startswith(".."):  # target escapes the checkout root — not ours to judge
        return False
    try:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", manifest_text.splitlines())
        return spec.match_file(rel)
    except Exception:
        return False  # any matcher error → fail open


# A new node under one of the four doc dirs must be CREATED through `mise run docs:new`
# (spec-doc-node-cli): the tool stamps the collision-free YYYY-MM-DD-HHMMSS filename (unique
# across every worktree), the canonical <type>-<slug> id, and the per-type frontmatter from
# the template — all of which a hand Write drifts from (and a same-second hand write collides
# at rebase). This subsumes the older legacy-name block: docs:new can only produce a valid
# name. EDITING an existing node stays a direct edit (Edit, or a Write whose target exists).
_DOC_NODE_PATH = re.compile(r"(^|/)docs/(specs|decisions|memory|knowledge)/[^/]+\.md$")
_ALLOW_DOC_WRITE = "AGENTIC_ALLOW_DOC_WRITE"

_DOC_CREATE_BLOCKED = (
    "BLOCKED: create a docs/{specs,decisions,memory,knowledge}/ node with "
    "`mise run docs:new <type> --json '{\"name\": \"…\", \"body\": \"…\"}'`, not a direct "
    "Write. docs:new stamps the collision-free YYYY-MM-DD-HHMMSS filename (unique across every "
    "worktree), the canonical <type>-<slug> id, and the per-type frontmatter from the "
    "template; a hand-written node drifts and can collide on the timestamp at rebase "
    "(spec-doc-node-cli). EDITING an existing doc is fine — use Edit, or Write a path that "
    "already exists. Human/migration override: AGENTIC_ALLOW_DOC_WRITE=1."
)


def _doc_creation_blocked(tool_name, tool_input, cwd):
    """Block CREATING a new node under docs/{specs,decisions,memory,knowledge}/ via the Write
    tool — agents create through `mise run docs:new` (spec-doc-node-cli). Editing an existing
    doc is allowed: Edit/MultiEdit, or a Write whose target already exists (a full rewrite).
    Templates (`_`-prefixed) are exempt; the env hatch is for the human / a migration."""
    if tool_name != "Write":
        return False
    if os.environ.get(_ALLOW_DOC_WRITE) == "1":
        return False
    file_path = tool_input.get("file_path") or ""
    norm = str(file_path).replace("\\", "/")
    if not _DOC_NODE_PATH.search(norm):
        return False
    if norm.rsplit("/", 1)[-1].startswith("_"):  # _template.md.tmpl and other scaffolds
        return False
    abs_path = norm if os.path.isabs(norm) else os.path.normpath(os.path.join(str(cwd or ""), norm))
    return not os.path.exists(abs_path)  # block only a CREATION (the target does not yet exist)


def is_env_file_access(tool_name, tool_input):
    """Block tool access to .env files (allows .env.sample / .env.example).

    Scoped to FILE tools. Bash .env access is already covered by the default-deny
    allowlist (cat/cp/mv against .env are not sanctioned operations)."""
    if tool_name not in ("Read", "Edit", "MultiEdit", "Write"):
        return False
    file_path = tool_input.get("file_path", "")
    return ".env" in file_path and not (
        file_path.endswith(".env.sample") or file_path.endswith(".env.example")
    )


def _log(input_data):
    """Append the tool call to logs/pre_tool_use.json (best-effort)."""
    try:
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "pre_tool_use.json"
        if log_path.exists():
            try:
                log_data = json.loads(log_path.read_text())
            except (json.JSONDecodeError, ValueError):
                log_data = []
        else:
            log_data = []
        log_data.append(input_data)
        log_path.write_text(json.dumps(log_data, indent=2))
    except Exception:
        pass  # logging must never break the session


def _fallback_decision(command: str) -> int:
    """Exit code to use when the policy engine could not be consulted."""
    head = command.strip().split()
    if not head:
        return 0
    prog = head[0]
    if prog in _FALLBACK_READ_ONLY:
        return 0
    if prog == "git" and len(head) > 1 and head[1] in _FALLBACK_GIT_READ_ONLY:
        return 0
    print(
        "BLOCKED: command policy unavailable; denying by default (fail-closed). "
        "See .claude/hooks/policy/allowlist.toml.",
        file=sys.stderr,
    )
    return 2


def _resolve_gitdir(base):
    """The gitdir for the checkout at ``base`` — walk up to the first ``.git``, following
    a linked-worktree ``.git`` FILE (``gitdir: <path>``) to its real gitdir. ``None`` if
    not found. Pure filesystem: mirrors ``git rev-parse --git-dir`` for the normal +
    linked-worktree cases without spawning git (keeps the guard dep-free)."""
    cur = os.path.abspath(base or ".")
    while True:
        dotgit = os.path.join(cur, ".git")
        if os.path.isdir(dotgit):
            return dotgit
        if os.path.isfile(dotgit):
            try:
                line = Path(dotgit).read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if line.startswith("gitdir:"):
                p = line.split(":", 1)[1].strip()
                return p if os.path.isabs(p) else os.path.normpath(os.path.join(cur, p))
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


# Age floor below which an ``index.lock`` is treated as a possibly-live git op and
# left alone. MUST equal ``STALE_LOCK_MIN_AGE_SECONDS = 6.0`` in ``scripts/wt.py`` and
# ``STALE_LOCK_MIN_AGE`` in ``crates/agenticd-git/src/lock.rs``: the daemon, the
# ``wt:unlock`` CLI, and this guard share one definition of "dead lock" so none races
# a live op (spec-git-index-lock-agent-contention).
STALE_LOCK_MIN_AGE_SECONDS = 6.0


def _git_index_lock_blocks(command, cwd):
    """Whether a git ``index.lock`` should BLOCK this mutation — and self-heal a dead one.

    A lock younger than ``STALE_LOCK_MIN_AGE_SECONDS`` may belong to a live/in-flight git
    op (the agent's own, or a daemon rebase): it BLOCKS, the reason the concurrency guard
    exists (spec-git-mutation-concurrency-guard). A lock at/over the threshold is dead —
    left by a crashed/killed git op — so it is CLEARED (matching ``wt:unlock`` and the
    daemon's ``clear_stale_index_lock``) and does NOT block: the agent no longer has to
    hand-run ``wt:unlock`` for a lock the rest of the system already auto-heals
    (spec-git-index-lock-agent-contention).

    Skew-safe: a future mtime yields a negative age (< the threshold) ⇒ treated as young
    ⇒ left alone, never yanked. Any failure (resolution / stat / remove) ⇒ ``False``: this
    preflight only ADDS protection, so it must never wrongly block a legitimate op — the
    background-mutation deny stays the primary guard."""
    try:
        base = command_policy.effective_base_dir(command, cwd) or cwd or os.getcwd()
        gitdir = _resolve_gitdir(base)
        if not gitdir:
            return False
        lock = os.path.join(gitdir, "index.lock")
        try:
            age = time.time() - os.stat(lock).st_mtime
        except FileNotFoundError:
            return False
        if age < STALE_LOCK_MIN_AGE_SECONDS:
            return True  # possibly-live op — never race it
        try:
            os.remove(lock)  # dead lock: heal it like wt:unlock instead of forcing one
        except OSError:
            return False  # fail open — surface as allowed rather than wrongly block
        return False
    except Exception:
        return False


def _allowlist_edit(tool_name: str, tool_input: dict) -> bool:
    """True when a file tool targets a worktree's capability allowlist
    (`.claude/hooks/policy/allowlist.toml`). Editing it changes what the agent may
    run, so the edit is gated behind a one-time human approval — after which the
    worktree allowlist is trusted and stays in sync with main unless the human
    approves a further change (decision-worktree-local-policy-resolution)."""
    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return False
    fp = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "").replace("\\", "/")
    return fp.endswith(".claude/hooks/policy/allowlist.toml") and command_policy.is_worktree_path(fp)


def _ask_user(reason: str) -> None:
    """Emit a PreToolUse ``ask`` decision so Claude Code prompts the human to
    approve (their "allow always" choice persists). Used for a capability the repo's
    main allowlist denies but the *worktree's* allowlist grants: a self-granted
    capability is never silently honored — it warrants one explicit human approval
    (decision-worktree-local-policy-resolution)."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


# ---- No file edits while a commit gate is running (lefthook stash race) --------
# General to any repo using the bundled lefthook pre-commit gate: lefthook stashes
# UNSTAGED changes for the gate's duration and pops them on exit, so a file edit landed
# mid-gate writes into the stashed-away baseline; the pop then merges it with the
# restored diff while the gate compiles a torn tree (a caller of a method whose
# definition was stashed → a phantom "method not found"). See docs/TOOLS.md §6.2.

_ALLOW_EDIT_DURING_COMMIT = "AGENTIC_ALLOW_EDIT_DURING_COMMIT"

_EDIT_DURING_COMMIT_BLOCKED = (
    "BLOCKED: a commit gate is running in this checkout — editing now corrupts it. "
    "lefthook stashes your UNSTAGED changes for the duration of the pre-commit gate and "
    "pops them on exit, so an edit landed mid-gate writes into the stashed-away baseline; "
    "the pop then merges it with the restored diff while the gate compiles a torn tree (a "
    "caller of a method whose definition was stashed → a phantom 'method not found'). Let "
    "the commit finish, then edit — and commit synchronously in the foreground so this "
    f"can't arise. Deliberate override: {_ALLOW_EDIT_DURING_COMMIT}=1."
)


def _checkout_root(base):
    """The working-tree root containing ``base`` — the nearest ancestor with a ``.git``
    entry (a dir in the main checkout, a file in a linked worktree). ``None`` if none.
    Pure filesystem; resolves even for a not-yet-created file (walks string ancestors)."""
    cur = os.path.abspath(base or ".")
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _common_gitdir(gitdir):
    """The shared/common git dir for ``gitdir``. For a linked-worktree gitdir
    (``<common>/worktrees/<name>``) that is ``<common>``; a normal ``.git`` is itself.
    lefthook writes its stash patch under the COMMON ``info/`` (git resolves ``info/`` to
    the common dir), so the patch path is shared across a repo's worktrees."""
    parent = os.path.dirname(gitdir.rstrip(os.sep))
    if os.path.basename(parent) == "worktrees":
        return os.path.dirname(parent)
    return gitdir


def _is_commit_proc(cmdline):
    """A live lefthook gate, or the ``git commit`` that spawned it."""
    c = cmdline.lower()
    if "lefthook" in c:
        return True
    return "git" in c and "commit" in c


def _within(path, root):
    """Is ``path`` inside (or equal to) ``root``? Realpath-normalized; False on error."""
    try:
        root = os.path.realpath(root)
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except Exception:
        return False


def _running_commit_procs(proc_root="/proc"):
    """(cmdline, cwd) for every live process, read from ``/proc``. Best-effort: an
    unreadable proc — or no ``/proc`` (non-Linux) — yields nothing, so the guard fails
    open. This liveness check makes the guard stale-safe: a crashed commit's leftover
    stash patch never blocks edits, because no live commit process backs it."""
    out = []
    try:
        pids = os.listdir(proc_root)
    except Exception:
        return out
    for pid in pids:
        if not pid.isdigit():
            continue
        d = os.path.join(proc_root, pid)
        try:
            with open(os.path.join(d, "cmdline"), "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except Exception:
            continue
        if not cmdline:
            continue
        try:
            cwd = os.readlink(os.path.join(d, "cwd"))
        except Exception:
            cwd = ""
        out.append((cmdline, cwd))
    return out


def _gate_in_flight(root, patch_present, procs):
    """Pure decision: is a commit gate live for the working tree ``root``? Requires BOTH
    lefthook's stash patch (present only between stash and pop) AND a live commit/lefthook
    process whose cwd is inside ``root``. The process check makes it stale-safe and scopes
    it to THIS worktree — the patch path is shared across a repo's worktrees, so a sibling
    worktree's commit (cwd elsewhere) must not block edits here."""
    if not root or not patch_present:
        return False
    return any(
        cwd and _is_commit_proc(cmdline) and _within(cwd, root)
        for cmdline, cwd in procs
    )


def _commit_gate_running(abs_path):
    """Whether a lefthook commit gate is live for the checkout containing ``abs_path``.
    The shared stash patch is a cheap pre-filter — absent in the common case, so no /proc
    scan; only when it exists do we resolve the worktree root and scan processes."""
    base = os.path.dirname(abs_path) or "."
    gitdir = _resolve_gitdir(base)
    if not gitdir:
        return False
    patch = os.path.join(_common_gitdir(gitdir), "info", "lefthook-unstaged.patch")
    if not os.path.exists(patch):
        return False
    return _gate_in_flight(_checkout_root(base), True, _running_commit_procs())


def _edit_during_commit_blocked(tool_name, tool_input, cwd):
    """Block a file-write tool while a lefthook commit gate is live in its checkout."""
    if tool_name not in _FILE_WRITE_TOOLS:
        return False
    if os.environ.get(_ALLOW_EDIT_DURING_COMMIT) == "1":
        return False
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return False
    fp = str(file_path).replace("\\", "/")
    if not os.path.isabs(fp):
        fp = os.path.normpath(os.path.join(str(cwd or ""), fp))
    return _commit_gate_running(fp)


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail_safe(exc, "pre_tool_use")
        return

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {}) or {}

    # 0. Repo-local PRE extension — runs before the bundle's built-in guards so a repo
    # can add machine-specific blocks (it exits 2 to block). Seed-once, repo-owned.
    invoke_extension("pre_tool_use", "pre", input_data)

    # 1. .env guard for file tools.
    if is_env_file_access(tool_name, tool_input):
        print("BLOCKED: Access to .env files containing sensitive data is prohibited", file=sys.stderr)
        print("Use .env.sample for template files instead", file=sys.stderr)
        sys.exit(2)

    # 2. No file edits on the main checkout — only inside a worktree (R7).
    if _main_tree_edit_blocked(tool_name, tool_input, input_data.get("cwd", "")):
        print(_MAIN_EDIT_BLOCKED, file=sys.stderr)
        _log(input_data)
        sys.exit(2)

    # 2a. No file edits while a commit gate is running in this checkout — lefthook
    # stashes unstaged changes for the gate's duration, so a mid-gate edit corrupts the
    # stash/pop (docs/TOOLS.md §6.2). Stale-safe + per-worktree via a live-process check.
    if _edit_during_commit_blocked(tool_name, tool_input, input_data.get("cwd", "")):
        print(_EDIT_DURING_COMMIT_BLOCKED, file=sys.stderr)
        _log(input_data)
        sys.exit(2)

    # 2b. Creating a new node under docs/{specs,decisions,memory,knowledge}/ must go through
    # `mise run docs:new` (spec-doc-node-cli) — a direct Write of a NEW doc is blocked and
    # redirected; editing an existing doc stays allowed.
    if _doc_creation_blocked(tool_name, tool_input, input_data.get("cwd", "")):
        print(_DOC_CREATE_BLOCKED, file=sys.stderr)
        _log(input_data)
        sys.exit(2)

    # 2c. No hand-edits of generated files — regenerate via the generator
    # (spec generated-files-manifest-and-edit-block).
    if _generated_file_edit_blocked(tool_name, tool_input, input_data.get("cwd", "")):
        print(_GENERATED_EDIT_BLOCKED, file=sys.stderr)
        _log(input_data)
        sys.exit(2)

    # 2d. Editing a worktree's capability allowlist requires a one-time human
    # approval. The Bash gate below reads the worktree's allowlist directly, so the
    # only way the agent widens its own surface is by editing that file — gate the
    # edit, and the approval is internalized (decision-worktree-local-policy-resolution).
    if _allowlist_edit(tool_name, tool_input):
        _ask_user(
            "This edits a worktree's capability allowlist "
            "(`.claude/hooks/policy/allowlist.toml`), which changes what the agent is "
            "allowed to run without further prompts. Approve this allowlist change?"
        )
        _log(input_data)
        sys.exit(0)

    # 3. Default-deny allowlist for Bash.
    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        cwd = input_data.get("cwd", "")
        background = bool(tool_input.get("run_in_background"))

        # 3a. Don't let a `cd <main>` (or `git -C <main>`) carry the agent out of its
        # worktree into the rest of the repo. The Bash tool resets cwd to the worktree
        # each call, so the hop silently aims git/rg/ls at the main checkout — the
        # cwd-confusion time-sink. This is a path-escape concern, independent of command
        # classification, so it precedes the allowlist. Hatch mirrors AGENTIC_ALLOW_MAIN_EDIT.
        if os.environ.get("AGENTIC_ALLOW_CD_ESCAPE") != "1":
            escape = command_policy.worktree_escape_decision(command, cwd)
            if escape is not None:
                print(escape.message, file=sys.stderr)
                _log(input_data)
                sys.exit(2)

        # 3a2. Reading `$?` straight after a pipeline reports the pipe's LAST stage,
        # not the command you meant — the silent-commit-failure trap (`git commit … |
        # tail; echo "EXIT=$?"` prints EXIT=0 while HEAD never moved). Independent of
        # command classification, so it precedes the allowlist. Hatch mirrors the others.
        if os.environ.get("AGENTIC_ALLOW_PIPE_EXIT") != "1":
            pipe_exit = command_policy.pipeline_exit_status_decision(command)
            if pipe_exit is not None:
                print(pipe_exit.message, file=sys.stderr)
                _log(input_data)
                sys.exit(2)

            # 3a3. The companion to 3a2: a `git commit` / `mise run <gate>` piped into
            # another command (`… | tail`) masks its exit even when `$?` is never read —
            # the agent just eyeballs the trimmed output and proceeds past a red gate /
            # rejected commit. Deterministic block on a gate/commit as a non-final pipeline
            # stage (spec-no-pipe-gate-commands-prompt-rule); read-only `… | tail` is fine.
            pipe_cmd = command_policy.pipeline_masks_command_decision(command)
            if pipe_cmd is not None:
                print(pipe_cmd.message, file=sys.stderr)
                _log(input_data)
                sys.exit(2)

        try:
            # Judge against the WORKTREE's own allowlist (honoring a leading
            # `cd <worktree> &&`). The only way an agent widens this surface is by
            # editing that allowlist.toml — gated by the one-time human approval
            # above (step 2c) — so the worktree allowlist is always either == main or
            # an approved divergence (decision-worktree-local-policy-resolution). The
            # engine and the main-tree-edit guard stay main-governed.
            policy = command_policy.load_policy(
                command_policy.resolve_allowlist_path(command, cwd)
            )
            in_worktree = command_policy.effective_in_worktree(command, cwd)
            # The index.lock preflight is only meaningful for a git mutation; skip the
            # filesystem walk otherwise (spec-git-mutation-concurrency-guard). A young
            # lock blocks; a stale (dead) one is healed in place and does not
            # (spec-git-index-lock-agent-contention).
            index_lock_present = (
                _git_index_lock_blocks(command, cwd)
                if command_policy.command_has_git_mutation(command, policy)
                else False
            )
            decision = command_policy.evaluate_bash(
                command,
                in_worktree=in_worktree,
                policy=policy,
                background=background,
                index_lock_present=index_lock_present,
                allow_main_commit=os.environ.get("AGENTIC_ALLOW_MAIN_COMMIT") == "1",
            )
        except Exception as exc:  # engine/config failure → fail-mode fallback
            try:
                log_dir = Path.cwd() / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / ".hook-errors.log").open("a", encoding="utf-8").write(
                    f"pre_tool_use | command_policy | {type(exc).__name__}: {exc}\n"
                )
            except Exception:
                pass
            sys.exit(_fallback_decision(command))

        if not decision.allowed:
            print(decision.message, file=sys.stderr)
            _log(input_data)
            sys.exit(2)

    # Repo-local POST extension — runs after every built-in guard passed.
    invoke_extension("pre_tool_use", "post", input_data)

    _log(input_data)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        fail_safe(exc, "pre_tool_use")
