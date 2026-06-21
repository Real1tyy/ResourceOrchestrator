#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Default-deny command policy engine for the PreToolUse hook (spec 0031).

The allowlist lives in ``.claude/hooks/policy/allowlist.toml`` (DATA). This module
is the ENGINE that interprets it: it parses a Bash command into sub-commands and
decides, for each, ``ALLOW`` or ``DENY``. Whatever is not explicitly allowed is
denied — the inverse of the old blocklist.

Design notes (so this ports cleanly into ``agenticd-policy``, ARCH §18):

* The *allowlist* (allowed tasks / read-only commands / scripts) is pure data.
* The *destructive denials* are mostly data (regex ``deny_rules``); a few
  token-precise git cases (force-push vs ``--force-with-lease``, ``reset --hard``,
  ``branch -D``, the commit-location guard) live here in code because a regex
  handles them poorly. Each is documented inline.
* Fail mode (spec 0031 open question): on an internal error or unparsable shell,
  fail *closed* for mutating commands and *open* for read-only ones — a hook bug
  degrades focus, never bricks inspection.

``evaluate_bash`` is pure and side-effect-free so the test suite can drive it with
crafted commands and an explicit ``in_worktree`` flag.
"""

from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Convention this whole workflow rests on: real work happens in a worktree under
# .claude/worktrees/ (CLAUDE.md § Worktree Convention).
WORKTREE_MARKER = ".claude/worktrees/"

DEFAULT_ALLOWLIST = Path(__file__).resolve().parent.parent / "policy" / "allowlist.toml"

# A leading `FOO=bar` environment assignment before the real program.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shell tokens that separate one sub-command from the next.
_SEPARATORS = {"&&", "||", "|", "&", ";", "(", ")", "\n"}

# Tokens that end one *statement* for the pipeline-exit-status guard. Deliberately
# EXCLUDES ``|``: a pipe continues the same statement, and a statement that contains a
# ``|`` is the pipeline whose ``$?`` is the last stage's status (not the first command's).
_STMT_SEPARATORS = {"&&", "||", "&", ";", "(", ")", "\n"}

# shlex (punctuation_chars) returns a *run* of operator chars as one token — ``);`` and
# ``|;`` arrive glued. The pipeline-exit-status guard needs them atomic to see the
# boundary, so a pure-operator token is re-split into its constituent operators. Words and
# redirections (``2>&1``, ``>&``, ``>>`` — they carry ``<``/``>``, not in this set) are
# left opaque, so a redirection is never mistaken for a statement boundary.
_OPERATOR_CHARS = frozenset(";|&()\n")
_OPERATOR_RUN_RE = re.compile(r"&&|\|\||[;()|&\n]")

# Shell control-flow keywords. A loop or conditional is exactly as dangerous as the
# commands in its body, so these are NOT treated as programs: the structural prefix
# is stripped and the residual (the body command) is classified normally. This lets
# agents script with for/while/if while every command they actually RUN still passes
# through the allowlist. Grammar, not policy — hence code, not allowlist.toml.
#   _KW_CMD_PREFIX : precede a command in the same group (`do cat x`, `if grep …`).
#   _KW_HEADER     : the group is iteration/branch setup, not a command
#                    (`for X in …`, `case X in`) — a structural no-op.
#   _KW_NOOP       : standalone structural tokens that execute nothing.
#   _KW_LOOP_CTRL  : loop-flow statements (`break`, `continue`, incl. `break 2`) —
#                    they run no external program, so a loop the agent is already
#                    allowed to run can control its own flow (spec-agent-loop-defriction).
_KW_CMD_PREFIX = frozenset({"if", "then", "elif", "else", "do", "while", "until", "!", "time", "{"})
_KW_HEADER = frozenset({"for", "select", "case"})
_KW_NOOP = frozenset({"done", "fi", "esac", "in", "}"})
_KW_LOOP_CTRL = frozenset({"break", "continue"})

# The actionable tail every default-deny message carries (risk mitigation: a deny
# is useless unless it names the way forward).
_ALTERNATIVES = (
    "Allowed: `mise run wt:new|wt:rebase|wt:verify|wt:list`, read-only "
    "inspection (ls, cat, rg, grep, git status/log/diff/show), and sanctioned git "
    "ops inside a worktree. See .claude/hooks/policy/allowlist.toml."
)


@dataclass(frozen=True)
class Decision:
    """Outcome of evaluating one command (or sub-command)."""

    allowed: bool
    message: str = ""
    rule_id: str = ""

    @staticmethod
    def allow(rule_id: str = "allow") -> "Decision":
        return Decision(allowed=True, rule_id=rule_id)

    @staticmethod
    def deny(message: str, rule_id: str) -> "Decision":
        return Decision(allowed=False, message=message, rule_id=rule_id)


@dataclass
class Policy:
    mise_tasks: frozenset[str]
    mise_subcommands: frozenset[str]
    read_only_commands: frozenset[str]
    git_read_only: frozenset[str]
    git_worktree_write: frozenset[str]
    scripts: frozenset[str]
    script_runners: frozenset[str]
    cargo_subcommands: frozenset[str] = frozenset()
    pnpm_read_only: frozenset[str] = frozenset()
    agenticd_subcommands: frozenset[str] = frozenset()
    deny_rules: list[tuple[str, re.Pattern[str], str]] = field(default_factory=list)
    fail_mutating: str = "deny"
    fail_read_only: str = "allow"
    block_main_tree_edits: bool = True


def load_policy(path: Path | str = DEFAULT_ALLOWLIST) -> Policy:
    """Load and compile the allowlist TOML into a :class:`Policy`."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    if data.get("schema_version") != 1:
        raise ValueError(f"unsupported allowlist schema_version: {data.get('schema_version')!r}")

    allow = data.get("allow", {})
    fail = data.get("fail_mode", {})
    file_tools = data.get("file_tools", {})
    deny_rules = [
        (r["id"], re.compile(r["pattern"]), r["message"])
        for r in data.get("deny_rules", [])
    ]

    def fs(key: str) -> frozenset[str]:
        return frozenset(allow.get(key, []))

    return Policy(
        mise_tasks=fs("mise_tasks"),
        mise_subcommands=fs("mise_subcommands"),
        read_only_commands=fs("read_only_commands"),
        git_read_only=fs("git_read_only"),
        git_worktree_write=fs("git_worktree_write"),
        scripts=frozenset(_norm_script(s) for s in allow.get("scripts", [])),
        script_runners=fs("script_runners"),
        cargo_subcommands=fs("cargo_subcommands"),
        pnpm_read_only=fs("pnpm_read_only"),
        agenticd_subcommands=fs("agenticd_subcommands"),
        deny_rules=deny_rules,
        fail_mutating=fail.get("mutating", "deny"),
        fail_read_only=fail.get("read_only", "allow"),
        block_main_tree_edits=file_tools.get("block_main_tree_edits", True),
    )


def _norm_script(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def is_worktree_path(path: str | None) -> bool:
    """True when ``path`` is inside a worktree under .claude/worktrees/."""
    if not path:
        return False
    return WORKTREE_MARKER in str(path).replace("\\", "/")


def main_root_from_cwd(cwd: str | None) -> str:
    """The main checkout root, derived from ``cwd``.

    If ``cwd`` is inside a worktree (``.../​.claude/worktrees/<slug>/...``), the
    main root is the path before that marker; otherwise ``cwd`` is itself the root.
    No git call — pure string work, so it is deterministic and testable.
    """
    if not cwd:
        return ""
    c = str(cwd).replace("\\", "/")
    if WORKTREE_MARKER in c:
        return c.split(WORKTREE_MARKER)[0].rstrip("/")
    return c.rstrip("/")


def is_main_tree_edit(file_path: str | None, cwd: str | None) -> bool:
    """True when a FILE-tool edit targets the main checkout (outside any worktree).

    The file-tool analogue of the no-commit-on-main guard (spec 0031 R7): under the
    worktree workflow an agent must edit files under ``.claude/worktrees/<slug>/``,
    never the main working tree. A path inside a worktree, or wholly outside the
    repo, is not a main-tree edit.
    """
    if not file_path:
        return False
    fp = str(file_path).replace("\\", "/")
    if WORKTREE_MARKER in fp:
        return False  # editing inside a worktree — the sanctioned location
    main_root = main_root_from_cwd(cwd)
    if not main_root:
        return False
    if not fp.startswith("/"):  # resolve a relative path against cwd
        fp = os.path.normpath(os.path.join(str(cwd or ""), fp)).replace("\\", "/")
    return fp == main_root or fp.startswith(main_root + "/")


def effective_base_dir(command: str, cwd: str | None) -> str:
    """The directory a command effectively runs in.

    Honors a leading ``cd <dir> &&`` (the agent may ``cd`` into the worktree before a
    git op); otherwise the hook-provided ``cwd``. Used both for the worktree check and
    to locate the gitdir for the index.lock preflight (spec-git-mutation-concurrency-guard).
    """
    base = cwd or ""
    try:
        head = _tokenize(command)
        # Leading `cd <dir>` (optionally before a separator) redirects the base.
        if len(head) >= 2 and head[0] == "cd" and head[1] not in _SEPARATORS:
            target = head[1]
            base = target if os.path.isabs(target) else os.path.join(cwd or "", target)
    except ValueError:
        pass
    return base


def effective_in_worktree(command: str, cwd: str | None) -> bool:
    """Resolve whether the command effectively runs inside a worktree."""
    base = effective_base_dir(command, cwd)
    return is_worktree_path(base) or is_worktree_path(cwd)


def worktree_root_from_cwd(path: str | None) -> str | None:
    """The worktree root (``.../.claude/worktrees/<slug>``) when ``path`` is inside a
    worktree, else ``None``. Pure string work, like :func:`main_root_from_cwd`."""
    if not path:
        return None
    c = str(path).replace("\\", "/")
    if WORKTREE_MARKER not in c:
        return None
    before, after = c.split(WORKTREE_MARKER, 1)
    slug = after.split("/", 1)[0]
    if not slug:
        return None
    return f"{before}{WORKTREE_MARKER}{slug}"


def resolve_allowlist_path(command: str, cwd: str | None) -> Path:
    """The allowlist a Bash command is judged against: the worktree's own installed
    allowlist when the command effectively runs inside a worktree (honoring a leading
    ``cd <worktree> &&``), else :data:`DEFAULT_ALLOWLIST`. The worktree allowlist is
    trusted because *editing* it is itself gated behind a one-time human approval
    (the file-tool trap in ``pre_tool_use``), so it is always either == main or an
    approved divergence (decision-worktree-local-policy-resolution). The policy
    ENGINE and the main-tree-edit guard are always main-governed."""
    base = effective_base_dir(command, cwd)
    wt = worktree_root_from_cwd(base) or worktree_root_from_cwd(cwd)
    if wt:
        candidate = Path(wt) / ".claude" / "hooks" / "policy" / "allowlist.toml"
        if candidate.is_file():
            return candidate
    return DEFAULT_ALLOWLIST


# ── worktree-escape guard ────────────────────────────────────────────────────────
# The single biggest time-sink observed in a busy multi-worktree session: the Bash
# tool resets cwd to the worktree on EVERY call, so a leading `cd <main-root> && …`
# silently points the rest of the command (git, rg, ls) at the MAIN checkout. The
# agent's own edits then read as "missing" and every `cd main && git …` also trips
# the busy main checkout's flickering index.lock — manufacturing a false "a daemon
# reverted my work" panic while the work sits intact in the worktree.
#
# We block ONLY a hop that lands inside the repo but OUTSIDE this worktree — the main
# checkout or a sibling worktree, the paths with confusing git state and the flickering
# lock. Truly-external paths (/tmp, ~, another project) are harmless scratch and stay
# allowed: the confusion is repo-internal by nature, so that is the only thing worth
# denying. Two hop forms are covered: `cd` (sticky — changes cwd for the rest of the
# command) and `git -C <dir>` (per-command — the same misdirection for one git call).
_CD_ESCAPE = (
    "BLOCKED: `cd {target}` points into the main checkout (or a sibling worktree), "
    "outside your worktree ({wt}). The Bash tool resets cwd to your worktree on EVERY "
    "call, so a `cd` out of it aims the rest of the command (git, rg, ls) at the MAIN "
    "checkout — your own edits then look 'missing' and you trip the busy main checkout's "
    "flickering index.lock, faking a 'my work was reverted' panic when it is intact in "
    "the worktree. Stay in your worktree: run commands bare (cwd is already the "
    "worktree), or pass an ABSOLUTE worktree path to the tool. Escaping to a truly "
    "external dir (/tmp, ~) is fine. Human escape hatch: AGENTIC_ALLOW_CD_ESCAPE=1."
)
_GIT_C_ESCAPE = (
    "BLOCKED: `git -C {target}` runs git against the main checkout (or a sibling "
    "worktree), outside your worktree ({wt}) — it shows/changes the WRONG checkout's "
    "state, the per-command form of the `cd <main>` confusion. Drop the `-C` to operate "
    "on your worktree, or `-C` a path inside it. Human escape hatch: "
    "AGENTIC_ALLOW_CD_ESCAPE=1."
)


def _within(path: str, root: str) -> bool:
    return bool(root) and (path == root or path.startswith(root + "/"))


def _resolve_cd_target(target: str | None, base: str | None) -> str | None:
    """Resolve a hop target against ``base`` (forward-slashed, no trailing slash), or
    ``None`` when it is unclassifiable as a repo path: a bare ``cd`` / ``cd -`` (home /
    ``$OLDPWD``), a ``~``/``$VAR`` target, or a relative target with no known ``base``
    (we already lost the cwd to a prior unresolvable hop). The guard only fires on a
    target it can prove is repo-internal, so ``None`` simply means "don't block"."""
    if target is None or target.startswith("~") or target.startswith("$"):
        return None
    if os.path.isabs(target):
        return os.path.normpath(target).replace("\\", "/")
    if base is None:
        return None
    return os.path.normpath(os.path.join(base, target)).replace("\\", "/")


def _git_dash_c_target(args: list[str]) -> str | None:
    """The ``-C <dir>`` target of a ``git`` invocation (the cwd-redirect — the true `cd`
    analogue), or ``None``. ``--git-dir`` is deliberately NOT covered: a linked worktree's
    gitdir lives under the MAIN repo's ``.git/worktrees/<slug>`` and would false-positive."""
    i = 1  # args[0] == "git"
    while i < len(args):
        if args[i] == "-C" and i + 1 < len(args):
            return args[i + 1]
        i += 1
    return None


def worktree_escape_decision(command: str, cwd: str | None) -> Decision | None:
    """Deny a command that hops out of its worktree into the rest of the repo (main
    checkout or a sibling worktree). ``None`` when there is nothing to enforce (cwd not
    in a worktree, no offending hop, or unparsable input — this guard only ADDS
    protection, so it never blocks on uncertainty). Sequential ``cd``s chain (each
    relative target resolves from the previous one's destination), so ``cd sub && cd ..``
    correctly stays inside. Pure and side-effect-free for the tests."""
    wt_root = worktree_root_from_cwd(cwd)
    if not wt_root:
        return None
    main_root = main_root_from_cwd(cwd)
    base: str | None = str(cwd or "").replace("\\", "/").rstrip("/")
    try:
        groups = _split_subcommands(_tokenize(command))
    except ValueError:
        return None

    def escapes(resolved: str | None) -> bool:
        # Inside the repo tree, but not inside THIS worktree → the confusion zone.
        return resolved is not None and _within(resolved, main_root) and not _within(resolved, wt_root)

    for group in groups:
        body = _strip_shell_keywords(group)
        if body is None:
            continue
        prog, args = _program_and_args(body)
        if prog == "cd":
            target = next((a for a in args[1:] if not a.startswith("-")), None)
            resolved = _resolve_cd_target(target, base)
            if escapes(resolved):
                return Decision.deny(_CD_ESCAPE.format(target=target, wt=wt_root), "cd-escapes-worktree")
            base = resolved  # a later relative cd resolves from here (None ⇒ cwd now unknown)
        elif prog == "git":
            target = _git_dash_c_target(args)
            if target is not None and escapes(_resolve_cd_target(target, base)):
                return Decision.deny(_GIT_C_ESCAPE.format(target=target, wt=wt_root), "git-c-escapes-worktree")
    return None


_PIPE_EXIT_BLOCKED = (
    "BLOCKED: reading `$?` right after a pipeline reports the LAST pipe stage's exit "
    "status, not the command you care about. `foo | tail` makes `$?` *tail's* status, so "
    "a failing `foo` looks like it passed — the silent-commit-failure trap: a rejected "
    "`git commit … | tail` prints `EXIT=0` while `HEAD` never moved. Use `${PIPESTATUS[0]}` "
    "for the first stage's status, drop the pipe, or verify the real effect (e.g. "
    "`git log --oneline -1` to confirm HEAD advanced). Human override: AGENTIC_ALLOW_PIPE_EXIT=1."
)


def _atomize_operators(tokens: list[str]) -> list[str]:
    """Re-split glued operator-run tokens (``);`` → ``)``, ``;``) into atomic operators so
    the statement walk sees every boundary. Pure-operator tokens only; words and
    redirections are passed through untouched."""
    out: list[str] = []
    for tok in tokens:
        if tok and all(c in _OPERATOR_CHARS for c in tok):
            out.extend(_OPERATOR_RUN_RE.findall(tok))
        else:
            out.append(tok)
    return out


# A `<<TAG … TAG` heredoc. `\1` re-matches the (possibly empty) opening quote as the tag's
# closing quote; `\2` re-matches the tag on its terminator line. `.*?` is non-greedy so each
# heredoc binds to its OWN terminator, not a later one; `<<-` allows a tab-indented terminator.
_HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def _strip_heredocs(command: str) -> str:
    """Remove ``<<TAG … TAG`` heredoc blocks before shell tokenization.

    A heredoc *body* is data — a commit message, a file's contents — not shell syntax, yet
    ``shlex`` has no heredoc grammar: a lone apostrophe in the body (``don't``) reads as an
    unbalanced quote and aborts tokenization, and the pipeline guards fail OPEN on a parse
    error — so a `git commit … | tail` wrapped in a `|| (… <<EOF …)` fallback used to slip
    past unblocked. Stripping the body first lets the real pipe around the heredoc be seen.
    Pure; over-matching only ever removes *more* candidate text, never fabricates a pipe."""
    return _HEREDOC_RE.sub(" ", command)


def pipeline_exit_status_decision(command: str) -> Decision | None:
    """Deny reading ``$?`` in a statement that immediately follows a pipeline.

    ``$?`` after ``foo | tail`` is *tail's* exit status, not ``foo``'s — the trap where a
    rejected ``git commit … | tail`` reports ``EXIT=0`` while ``HEAD`` never moved
    (a real agent run masked two failed commits this way). Flagged only when the previous
    non-empty statement was a pipeline AND this statement reads ``$?``; the recommended
    fixes (``${PIPESTATUS[0]}``, dropping the pipe) carry no literal ``$?`` and are NOT
    flagged. ``None`` when there's nothing to flag (no pipeline-then-``$?`` sequence, or
    unparsable input — this guard only ADDS protection, so it never blocks on uncertainty).
    Pure and side-effect-free.

    Known gap: a single-quoted ``'$?'`` (which the shell would NOT expand) right after a
    pipeline is still flagged — accepted as a rare, harmless false positive, and the human
    hatch (``AGENTIC_ALLOW_PIPE_EXIT=1``) covers it."""
    try:
        tokens = _atomize_operators(_tokenize(_strip_heredocs(command)))
    except ValueError:
        return None

    prev_was_pipeline = False
    cur: list[str] = []
    cur_has_pipe = False

    def flush() -> Decision | None:
        nonlocal prev_was_pipeline, cur, cur_has_pipe
        if cur:  # skip empty statements so a bare `)` / `;;` doesn't reset the flag
            if prev_was_pipeline and any("$?" in tok for tok in cur):
                return Decision.deny(_PIPE_EXIT_BLOCKED, "pipeline-masks-exit-status")
            prev_was_pipeline = cur_has_pipe
        cur = []
        cur_has_pipe = False
        return None

    for tok in tokens:
        if tok == "|":
            cur_has_pipe = True  # stays in the same statement
            continue
        if tok in _STMT_SEPARATORS:
            denied = flush()
            if denied is not None:
                return denied
            continue
        cur.append(tok)
    return flush()


_PIPE_MASKS_COMMAND = (
    "BLOCKED: piping `{cmd}` into another command masks its exit status. A pipeline's "
    "`$?` is its LAST stage's status, so `{cmd} … | tail` reports *tail's* exit (`0`) and "
    "silently swallows a failed commit / red gate — you read success while it actually "
    "failed. Run `{cmd}` BARE and read its exit code directly; for a multi-line commit "
    "message use `git commit -F /tmp/msg` (also sidesteps the heredoc/quoting friction "
    "here). Piping a READ-ONLY inspection (`git log | tail`, `git diff | head`) is fine — "
    "this only blocks a gate/commit as a non-final pipeline stage, whose exit you act on. "
    "Human override: AGENTIC_ALLOW_PIPE_EXIT=1."
)


def _segment_masked_command_label(segment: list[str]) -> str | None:
    """If ``segment`` (one pipeline stage's tokens) is a command whose exit status is
    load-bearing — ``git commit`` or ``mise run <task>`` — return a short human label for
    it, else ``None``. These are the commands an agent acts on the *success* of (a landed
    commit, a green gate); masking their exit behind a pipe is the footgun
    (spec-no-pipe-gate-commands-prompt-rule). Read-only programs (``tail``/``grep``/``git
    log``) are deliberately NOT load-bearing — piping them carries no exit code you act on."""
    program, args = _program_and_args(segment)
    if program == "git":
        sub, _, _ = _git_subcommand(args)
        return "git commit" if sub == "commit" else None
    if program == "mise":
        # First non-flag token after ``mise`` == ``run`` → a task run whose exit matters
        # (every gate in this repo is a ``mise run`` task; a read-only ``mise`` subcommand
        # like ``mise tasks`` is not ``run`` and is not flagged).
        rest = [a for a in args[1:] if not a.startswith("-")]
        return "mise run" if rest and rest[0] == "run" else None
    return None


def pipeline_masks_command_decision(command: str) -> Decision | None:
    """Deny a ``git commit`` / ``mise run`` that sits as a NON-FINAL stage of a pipeline.

    Sibling to :func:`pipeline_exit_status_decision`: that one catches reading ``$?`` after
    a pipeline; this one catches the more common variant where the agent never reads ``$?``
    at all — it just pipes the gate/commit to ``tail``/``head``/``grep`` to trim output and
    eyeballs the result. ``git commit … | tail`` and ``mise run <gate> 2>&1 | tail`` report
    the pipe's LAST stage (``0``), so a rejected commit / red gate looks green.

    Narrow by construction — only a *commit* or *mise task run* as a non-terminal stage is
    flagged; a bare commit/gate and a read-only ``… | tail`` (``git log | tail``) both pass.
    ``None`` when nothing is flagged or the input is unparsable (this guard only ADDS
    protection, never blocks on uncertainty). Pure and side-effect-free. The pipefail case
    (``set -o pipefail`` preserves the first failure) is the rare accepted false positive,
    covered by the ``AGENTIC_ALLOW_PIPE_EXIT=1`` hatch — mirrors the sibling guard."""
    try:
        tokens = _atomize_operators(_tokenize(_strip_heredocs(command)))
    except ValueError:
        return None

    segment: list[str] = []          # tokens of the current pipeline stage
    pipeline: list[list[str]] = []   # stages accumulated in the current statement

    def end_segment() -> None:
        nonlocal segment
        pipeline.append(segment)
        segment = []

    def flush_statement() -> Decision | None:
        nonlocal pipeline
        end_segment()
        if len(pipeline) > 1:  # a real pipeline — check every NON-final stage
            for stage in pipeline[:-1]:
                label = _segment_masked_command_label(stage)
                if label is not None:
                    return Decision.deny(
                        _PIPE_MASKS_COMMAND.format(cmd=label), "pipeline-masks-command"
                    )
        pipeline = []
        return None

    for tok in tokens:
        if tok == "|":
            end_segment()
            continue
        if tok in _STMT_SEPARATORS:
            denied = flush_statement()
            if denied is not None:
                return denied
            continue
        segment.append(tok)
    return flush_statement()


def _tokenize(command: str) -> list[str]:
    """Tokenize a shell command, keeping operators (``&&``, ``|``, ``;`` …) as
    their own tokens. Raises ``ValueError`` on unbalanced quotes."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _split_subcommands(tokens: list[str]) -> list[list[str]]:
    """Split a token stream into sub-commands on shell separators, dropping
    redirection operators and their targets (redirection is not classified —
    a documented interim gap; the .env guard still blocks .env writes)."""
    groups: list[list[str]] = []
    cur: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in _SEPARATORS:
            if cur:
                groups.append(cur)
                cur = []
        elif tok.startswith(">") or tok.startswith("<") or tok == "&>":
            # Redirection operator — skip it and its target token.
            i += 1
        else:
            cur.append(tok)
        i += 1
    if cur:
        groups.append(cur)
    return groups


def _program_and_args(group: list[str]) -> tuple[str | None, list[str]]:
    """Strip leading ``VAR=val`` assignments; return (program, args-incl-program)."""
    idx = 0
    while idx < len(group) and _ENV_ASSIGN_RE.match(group[idx]):
        idx += 1
    if idx >= len(group):
        return None, []
    args = group[idx:]
    return args[0], args


# ── git helpers ────────────────────────────────────────────────────────────────

def _git_subcommand(args: list[str]) -> tuple[str | None, list[str], str | None]:
    """From ``git`` args, return (subcommand, post-subcommand args, -C dir).

    Skips git global flags: ``-C <dir>``, ``-c <kv>``, ``--no-pager``, etc.
    ``-C`` redirects git's working directory, so we surface it for the worktree
    guard.
    """
    cdir: str | None = None
    i = 1  # args[0] == "git"
    while i < len(args):
        tok = args[i]
        if tok in ("-C", "-c"):
            if tok == "-C" and i + 1 < len(args):
                cdir = args[i + 1]
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok, args[i + 1:], cdir
    return None, [], cdir


def _has_short_flag(args: list[str], letter: str) -> bool:
    """True if a clustered short flag like ``-f`` / ``-fd`` carries ``letter``."""
    for a in args:
        if a.startswith("-") and not a.startswith("--") and letter in a[1:]:
            return True
    return False


_COMMIT_ON_MAIN = (
    "BLOCKED: no commits on the main checkout (spec 0031). Commits happen inside a "
    "worktree under .claude/worktrees/. Run `mise run wt:new <slug>` and commit from "
    "there. (Human escape hatch: AGENTIC_ALLOW_MAIN_COMMIT=1 for the lefthook guard.)"
)

# ── concurrency guard (spec-git-mutation-concurrency-guard) ─────────────────────────
# Two git ops must never touch the same index/refs at once. These rule_ids are the
# classifier's read-only / listing verdicts; a git command is a MUTATION iff
# _classify_git tags it as ANYTHING else (commit/add/rebase/reset/restore/push/… or a
# destructive deny). Reusing the classifier keeps a single source of truth — no second
# copy of the per-verb flag logic to drift out of sync.
_GIT_READONLY_RULE_IDS = frozenset(
    {"git-bare", "git-read-only", "git-branch-list", "git-worktree-list", "git-clean-dryrun"}
)

# Worktree-lifecycle mise tasks that run a git mutation under the hood (rebase/reset of
# the CURRENT worktree's branch), so backgrounding them is the same overlap hazard as a
# direct git mutation. `wt:new` is excluded — it builds a SEPARATE worktree (a different,
# lesser hazard), and including it would risk false blocks during legitimate parallel setup.
GIT_MUTATING_MISE_TASKS = frozenset({"wt:rebase", "gate:fix"})

_BACKGROUND_GIT_MUTATION = (
    "BLOCKED: don't run a git mutation in the background (run_in_background=true). A "
    "backgrounded git op keeps writing the index/refs after the tool call returns, so "
    "your next git command races it — the bug where two chunks land under one commit "
    "message and the other commit finds nothing staged. Commit synchronously: run git "
    "in the foreground and let it finish before issuing the next git command. "
    "(spec-git-mutation-concurrency-guard)"
)

_INDEX_LOCK_HELD = (
    "BLOCKED: a git index.lock is present in this worktree — another git op is in "
    "flight, or a previous one crashed and left a stale lock. Running this mutation now "
    "would race it (or fail with \"Unable to create '.../index.lock': File exists\"). "
    "Wait for an active op to finish; if the lock is stale, run `mise run wt:unlock` "
    "(it refuses to remove a lock younger than a live op). "
    "(spec-git-mutation-concurrency-guard)"
)


def _classify_git(args: list[str], in_worktree: bool, policy: Policy,
                  allow_main_commit: bool = False) -> Decision:
    sub, rest, cdir = _git_subcommand(args)
    # `git -C <worktree>` redirects git's CWD into a worktree.
    here = in_worktree or is_worktree_path(cdir)

    if sub is None:
        return Decision.allow("git-bare")  # `git`, `git --help`

    if sub in policy.git_read_only:
        return Decision.allow("git-read-only")

    # ── commit: the no-commit-on-main guard (R3) ────────────────────────────────
    if sub == "commit":
        if _has_short_flag(rest, "n"):  # `git commit -n` == --no-verify
            return Decision.deny(
                "Bypassing git hooks (git commit -n / --no-verify) is forbidden.",
                "no-verify",
            )
        if here:
            return Decision.allow("git-commit-in-worktree")
        # Human-approved direct-to-main commit (the /change fast path). The env hatch
        # AGENTIC_ALLOW_MAIN_COMMIT is read in the hook and plumbed in as
        # allow_main_commit, mirroring the edit guard's AGENTIC_ALLOW_MAIN_EDIT.
        # lefthook's guard:commit re-checks the same env var, so both layers agree.
        if allow_main_commit:
            return Decision.allow("git-commit-main-hatch")
        return Decision.deny(_COMMIT_ON_MAIN, "commit-on-main")

    # ── add: staging on main rides the same hatch as commit (R3) ────────────────
    # The /change fast path is documented as `git add <files>` then `git commit`, and
    # `git commit -a` cannot stage a NEW untracked file — so the AGENTIC_ALLOW_MAIN_COMMIT
    # hatch that opens `commit` on main must open its prerequisite `add` too, or the path
    # could never stage. Without the hatch (outside a worktree) `add` falls through to the
    # worktree-only guard below; in a worktree it is an ordinary sanctioned mutation.
    if sub == "add" and not here and allow_main_commit:
        return Decision.allow("git-add-main-hatch")

    # ── push: force-push blocked, --force-with-lease allowed (R4) ───────────────
    if sub == "push":
        has_lease = any(
            a == "--force-with-lease" or a.startswith("--force-with-lease=")
            or a == "--force-if-includes"
            for a in rest
        )
        has_force = ("--force" in rest or any(a.startswith("--force=") for a in rest)
                     or _has_short_flag(rest, "f"))
        if has_force and not has_lease:
            return Decision.deny(
                "force-push is blocked — it can clobber others' work. Use "
                "`git push --force-with-lease`.",
                "force-push",
            )
        return (Decision.allow("git-push-worktree") if here
                else Decision.deny("git push is only allowed from inside a worktree.", "push-on-main"))

    # ── reset --hard is destructive (R4) ────────────────────────────────────────
    if sub == "reset":
        if "--hard" in rest:
            return Decision.deny(
                "git reset --hard discards work irrecoverably and can rewind a branch "
                "to another ref; not allowed. Commit or `git stash` instead.",
                "reset-hard",
            )
        return _worktree_write(sub, here)

    # ── branch: list is read-only; -D / force-rename are destructive ────────────
    if sub == "branch":
        if "-D" in rest or "-M" in rest or ("--delete" in rest and "--force" in rest):
            return Decision.deny(
                "Force branch deletion/rename is forbidden. Branch teardown is the "
                "human's Stage-8 op (mise run wt:land).",
                "branch-force-delete",
            )
        mutating_flags = {"-d", "--delete", "-m", "--move", "-u", "--set-upstream-to",
                          "--unset-upstream", "-c", "--create-reflog", "-f", "--force"}
        if any(a in mutating_flags for a in rest):
            return _worktree_write(sub, here)
        return Decision.allow("git-branch-list")

    # ── worktree: list is read-only; add/remove go through wt:* tasks ───────────
    if sub == "worktree":
        sub2 = next((a for a in rest if not a.startswith("-")), None)
        if sub2 in (None, "list"):
            return Decision.allow("git-worktree-list")
        if sub2 == "remove" and ("--force" in rest or _has_short_flag(rest, "f")):
            return Decision.deny(
                "Force-removing a worktree can destroy uncommitted work. Teardown is "
                "the human's op: mise run wt:land <slug>.",
                "worktree-force-remove",
            )
        return Decision.deny(
            "Manage worktrees through the tasks, not raw git: `mise run wt:new <slug>` "
            "to create, `mise run wt:land <slug>` (human) to tear down.",
            "git-worktree-mutate",
        )

    # ── clean: -f caught by deny_rule; dry-run is fine; bare clean denied ────────
    if sub == "clean":
        if "-n" in rest or "--dry-run" in rest:
            return Decision.allow("git-clean-dryrun")
        return Decision.deny("git clean is forbidden (it deletes untracked files).", "git-clean")

    # ── checkout: mass-discard of the working tree is destructive ───────────────
    if sub == "checkout":
        if "." in rest:  # `git checkout .` / `git checkout -- .`
            return Decision.deny(
                "Mass-discarding the working tree (git checkout -- .) is forbidden. "
                "Commit or `git stash` first.",
                "checkout-discard",
            )
        return _worktree_write(sub, here)

    # ── remaining sanctioned in-worktree mutations (rebase, stash, fetch, …) ────
    if sub in policy.git_worktree_write:
        return _worktree_write(sub, here)

    return Decision.deny(
        f"git {sub} is not on the allowlist. Use the wt:* tasks or a read-only git "
        f"command. {_ALTERNATIVES}",
        "git-unknown",
    )


def _worktree_write(sub: str, here: bool) -> Decision:
    if here:
        return Decision.allow(f"git-{sub}-in-worktree")
    return Decision.deny(
        f"git {sub} mutates history/state and is only allowed inside a worktree under "
        f".claude/worktrees/. Create one with `mise run wt:new <slug>`.",
        "git-mutate-on-main",
    )


# ── mise helpers ─────────────────────────────────────────────────────────────

def _classify_mise(args: list[str], policy: Policy) -> Decision:
    # First non-flag token after `mise` is the subcommand.
    rest = args[1:]
    sub = next((a for a in rest if not a.startswith("-")), None)
    if sub is None:
        return Decision.allow("mise-bare")  # `mise`, `mise --version`

    if sub in ("run", "task"):
        after = rest[rest.index(sub) + 1:]
        task = next((a for a in after if not a.startswith("-")), None)
        if task is None:
            return Decision.allow("mise-run-bare")
        if task in policy.mise_tasks:
            return Decision.allow("mise-task")
        return Decision.deny(
            f"mise task '{task}' is not on the agent allowlist. {_ALTERNATIVES}",
            "mise-task-denied",
        )

    if sub in policy.mise_tasks:  # `mise <task>` shorthand
        return Decision.allow("mise-task-shorthand")

    if sub in policy.mise_subcommands:
        return Decision.allow("mise-subcommand")

    return Decision.deny(
        f"`mise {sub}` is not allowed (only task runs and read-only mise subcommands). "
        f"{_ALTERNATIVES}",
        "mise-denied",
    )


# ── cargo / pnpm helpers ─────────────────────────────────────────────────────

def _first_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """First non-flag token after the program and the tokens following it.

    Skips leading flags and a ``+toolchain`` selector (``cargo +nightly check``).
    """
    for i in range(1, len(args)):
        tok = args[i]
        if tok.startswith("-") or tok.startswith("+"):
            continue
        return tok, args[i + 1:]
    return None, []


def _classify_cargo(args: list[str], policy: Policy) -> Decision:
    """Allow the read-only / compile loop (``check``/``clippy``/``test``/``build``,
    plus ``tree``/``metadata`` and ``fmt --check``); deny verbs that mutate source,
    the lockfile, or the global env (``fmt`` write, ``run``, ``clean``, ``add``, …).
    Allowed anywhere — these touch only the gitignored ``target/`` cache, not git."""
    sub, rest = _first_subcommand(args)
    if sub is None:
        return Decision.allow("cargo-bare")  # `cargo`, `cargo --version`

    # `cargo fmt` rewrites source in place; the fix path is `mise run format`. Only
    # the read-only `--check` form (incl. `cargo fmt --all -- --check`) is allowed.
    if sub == "fmt":
        if "--check" in rest:
            return Decision.allow("cargo-fmt-check")
        return Decision.deny(
            "cargo fmt rewrites source files. Use `mise run format` to fix drift; "
            "`cargo fmt --check` (read-only) is allowed.",
            "cargo-fmt-write",
        )

    if sub in policy.cargo_subcommands:
        return Decision.allow("cargo-allowed")

    return Decision.deny(
        f"`cargo {sub}` is not on the allowlist (it mutates source/lockfile/env or "
        f"runs arbitrary code). Allowed: check, clippy, test, nextest, build, "
        f"fmt --check, tree, metadata. For the whole-workspace gate run "
        f"`mise run check:backend`. {_ALTERNATIVES}",
        "cargo-denied",
    )


def _classify_pnpm(args: list[str], policy: Policy) -> Decision:
    """Allow harmless read-only pnpm inspection (``list``/``why``/``outdated``/…);
    deny everything that mutates deps or runs scripts. Dependency changes go through
    ``mise run dep:add`` and frontend tests through ``mise run test:unit`` — bare
    ``pnpm add``/``install``/``exec``/``run`` stays denied (spec 0031)."""
    sub = _first_subcommand(args)[0]
    if sub is None:
        return Decision.allow("pnpm-bare")  # `pnpm`, `pnpm --version`
    if sub in policy.pnpm_read_only:
        return Decision.allow("pnpm-read-only")
    return Decision.deny(
        f"`pnpm {sub}` is not on the allowlist. Add deps with `mise run dep:add "
        f"<pkg>`; run frontend tests with `mise run test:unit`. Read-only pnpm "
        f"(list, why, outdated, licenses, …) is allowed. {_ALTERNATIVES}",
        "pnpm-denied",
    )


def _classify_agenticd(args: list[str], policy: Policy) -> Decision:
    """The project's own binary. Only the named, safe subcommands (`init`, `doctor`)
    are allowed — the edit-bundle→re-init synchronize loop
    (spec-docs-source-of-truth-inversion). A bare `agenticd` / `--version` / `--help`
    is read-only and allowed; mutating or fleet subcommands (`upgrade`, `serve`, …)
    stay denied as the human's call. Routed by the binary's basename, so the build
    forms (`./target/debug/agenticd init`) ride along."""
    sub = _first_subcommand(args)[0]
    if sub is None:
        return Decision.allow("agenticd-bare")  # `agenticd`, `agenticd --version`
    if sub in policy.agenticd_subcommands:
        return Decision.allow("agenticd-subcommand")
    allowed = ", ".join(sorted(policy.agenticd_subcommands)) or "(none)"
    return Decision.deny(
        f"`agenticd {sub}` is not an allowed subcommand. Allowed: {allowed}. "
        f"`agenticd init` syncs the managed docs + harness; other subcommands are "
        f"human-run. {_ALTERNATIVES}",
        "agenticd-denied",
    )


# ── scripts ────────────────────────────────────────────────────────────────────

def _classify_script_runner(prog: str, args: list[str], policy: Policy) -> Decision | None:
    """Allow `bash -n …`, and runners invoking an allow-listed script. Returns
    None if this isn't a runner invocation we recognize as allowed → caller denies."""
    if prog in ("bash", "sh") and "-n" in args:
        return Decision.allow("shell-syntax-check")
    for a in args[1:]:
        if _norm_script(a) in policy.scripts:
            return Decision.allow("allowed-script")
    return None


def _strip_shell_keywords(group: list[str]) -> list[str] | None:
    """Strip leading shell control-flow keywords; return the residual command to
    classify, or ``None`` when the group is purely structural (``done``, ``fi``, a
    ``for X in …`` header) and executes nothing. Subshell ``(``/``)`` are already
    consumed as separators, so the commands they wrap arrive as their own groups."""
    i = 0
    n = len(group)
    while i < n and group[i] in _KW_CMD_PREFIX:
        i += 1
    if i >= n or group[i] in _KW_HEADER or group[i] in _KW_NOOP or group[i] in _KW_LOOP_CTRL:
        return None
    return group[i:]


def classify_subcommand(group: list[str], in_worktree: bool, policy: Policy,
                        allow_main_commit: bool = False) -> Decision:
    # 1. Destructive denials first — scanned over the WHOLE group (keywords and all)
    #    so a dangerous token in a loop header can't slip past keyword-stripping.
    raw = " ".join(group)
    for rule_id, pattern, message in policy.deny_rules:
        if pattern.search(raw):
            return Decision.deny(message, rule_id)

    # 2. Peel shell control-flow grammar; classify the body command the loop/branch
    #    actually runs. A purely structural group (`done`, `for … in …`) is a no-op.
    body = _strip_shell_keywords(group)
    if body is None:
        return Decision.allow("shell-structural")

    prog, args = _program_and_args(body)
    if prog is None:
        return Decision.allow("empty")  # bare env-assignments / nothing to run

    # 3. Program-specific routing.
    if prog == "git":
        return _classify_git(args, in_worktree, policy, allow_main_commit)
    # Mise (our task orchestrator) is routed by basename, so an absolute-path invocation
    # (`/home/you/.local/bin/mise run <task>`) is classified exactly like bare
    # `mise run <task>`. Bare `mise` resolves via a shell FUNCTION that `mise activate`
    # defines; that function is not initialised in the agent's non-interactive Bash, so a
    # bare `mise run` can die with `command not found`. The robust escape is to call the
    # binary by its resolved path — which only works if the policy accepts the path. (Same
    # idea as `agenticd` below; deny_rules ran first over the raw string, so the human-only
    # e2e / remote-rebase denials still fire on an absolute-path form.)
    if os.path.basename(prog) == "mise":
        return _classify_mise(args, policy)
    if prog == "cargo":
        return _classify_cargo(args, policy)
    if prog == "pnpm":
        return _classify_pnpm(args, policy)
    if os.path.basename(prog) == "agenticd":
        return _classify_agenticd(args, policy)

    # 4. Direct invocation of an allow-listed script (scripts/wt.py, ./scripts/…).
    if _norm_script(prog) in policy.scripts:
        return Decision.allow("allowed-script")

    # 5. Script runners (bash -n, python3 scripts/specs.py, …).
    if prog in policy.script_runners:
        decision = _classify_script_runner(prog, args, policy)
        if decision is not None:
            return decision
        return Decision.deny(
            f"`{prog}` may only syntax-check (-n) or run an allow-listed script. {_ALTERNATIVES}",
            "runner-denied",
        )

    # 6. Read-only inspection programs.
    if prog in policy.read_only_commands:
        return Decision.allow("read-only")

    # 7. Default deny.
    return Decision.deny(
        f"BLOCKED (default-deny, spec 0031): `{prog}` is not on the allowlist. {_ALTERNATIVES}",
        "default-deny",
    )


def _looks_read_only(command: str, policy: Policy) -> bool:
    """Cheap, exception-proof check used only for the fail-mode fallback."""
    head = command.strip().split()
    if not head:
        return True
    prog = head[0]
    if prog in policy.read_only_commands:
        return True
    if prog == "git" and len(head) > 1 and head[1] in policy.git_read_only:
        return True
    return False


def _is_git_mutation(args: list[str], policy: Policy) -> bool:
    """True when a ``git`` invocation writes the index/refs/worktree.

    Single source of truth: a git command mutates iff :func:`_classify_git` does NOT tag
    it as a read-only / listing form. ``in_worktree=True`` so the verdict reflects the
    verb's NATURE, not where it runs (location routing is the classifier's separate job)."""
    return _classify_git(args, in_worktree=True, policy=policy).rule_id not in _GIT_READONLY_RULE_IDS


def _mise_task_name(args: list[str]) -> str | None:
    """The task a ``mise`` invocation runs: ``mise run <task>`` / ``mise task <task>`` /
    the ``mise <task>`` shorthand. ``None`` for bare/meta mise."""
    rest = args[1:]
    sub = next((a for a in rest if not a.startswith("-")), None)
    if sub is None:
        return None
    if sub in ("run", "task"):
        after = rest[rest.index(sub) + 1:]
        return next((a for a in after if not a.startswith("-")), None)
    return sub


def _groups_have_git_mutation(groups: list[list[str]], policy: Policy) -> bool:
    """True if any sub-command mutates git state — a direct ``git`` mutation or a
    git-mutating worktree-lifecycle mise task. Honors shell control-flow and a leading
    ``cd`` (the groups are already keyword-stripped and split by :func:`evaluate_bash`)."""
    for group in groups:
        body = _strip_shell_keywords(group)
        if body is None:
            continue
        prog, args = _program_and_args(body)
        if prog is None:
            continue
        if prog == "git" and _is_git_mutation(args, policy):
            return True
        # Basename match so an absolute-path `/…/mise run wt:rebase` is still seen as a
        # git mutation by the concurrency guard — consistent with the dispatcher above.
        if os.path.basename(prog) == "mise" and _mise_task_name(args) in GIT_MUTATING_MISE_TASKS:
            return True
    return False


def command_has_git_mutation(command: str, policy: Policy) -> bool:
    """True if any sub-command mutates git state. Pure; safe-False on unparsable input.
    The hook calls this to decide whether the index.lock preflight is worth doing."""
    try:
        groups = _split_subcommands(_tokenize(command))
    except ValueError:
        return False
    return _groups_have_git_mutation(groups, policy)


def evaluate_bash(
    command: str,
    *,
    in_worktree: bool,
    policy: Policy,
    background: bool = False,
    index_lock_present: bool = False,
    allow_main_commit: bool = False,
) -> Decision:
    """Evaluate a full Bash command. Every sub-command must be allowed; the first
    denial wins. Pure and side-effect free.

    ``background`` / ``index_lock_present`` carry the per-call concurrency facts the
    hook resolves (the tool's ``run_in_background`` flag, and whether a git ``index.lock``
    exists). Both default ``False`` so existing call sites are unchanged
    (spec-git-mutation-concurrency-guard)."""
    try:
        tokens = _tokenize(command)
        groups = _split_subcommands(tokens)
    except ValueError:
        # Unparsable shell (e.g. unbalanced quotes) — fail-mode fallback.
        if _looks_read_only(command, policy) and policy.fail_read_only == "allow":
            return Decision.allow("fail-open-read-only")
        return Decision.deny(
            "Could not parse this command safely; denying by default (fail-closed). "
            f"{_ALTERNATIVES}",
            "unparsable",
        )

    if not groups:
        return Decision.allow("empty")

    # Concurrency guard (spec-git-mutation-concurrency-guard): a git mutation must never
    # overlap another git op. In the agent's sequential tool loop the only overlap vector
    # is backgrounding one op; the only sign of an already-in-flight or crashed op is a
    # held index.lock. Both deny git MUTATIONS only — read-only git and non-git commands
    # are untouched (inspection is never gated). Background takes precedence when both hold.
    if (background or index_lock_present) and _groups_have_git_mutation(groups, policy):
        if background:
            return Decision.deny(_BACKGROUND_GIT_MUTATION, "background-git-mutation")
        return Decision.deny(_INDEX_LOCK_HELD, "index-lock-held")

    for group in groups:
        decision = classify_subcommand(group, in_worktree, policy, allow_main_commit)
        if not decision.allowed:
            return decision
    return Decision.allow("all-subcommands-allowed")
