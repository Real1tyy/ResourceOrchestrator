#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Unit tests for the default-deny command policy (spec 0031).

Two layers:
  * ``evaluate_bash`` directly — the engine, with an explicit ``in_worktree`` flag.
  * the ``pre_tool_use.py`` hook end-to-end — feed it ``PreToolUse`` JSON on stdin
    and assert the exit code (0 allow / 2 block), matching the acceptance criteria.

Run with: ``mise run test:hooks`` (or ``python3 .claude/hooks/tests/test_command_policy.py``).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR / "validators"))
sys.path.insert(0, str(HOOKS_DIR))

import command_policy  # noqa: E402
import pre_tool_use  # noqa: E402  (stdlib lock-resolution helpers; importing runs no main)

POLICY = command_policy.load_policy()
HOOK = HOOKS_DIR / "pre_tool_use.py"
# A representative absolute worktree path for cwd-sensitive cases.
WT = "/home/u/proj/.claude/worktrees/feat-x"
MAIN = "/home/u/proj"


def decide(cmd, in_worktree=False, *, background=False, index_lock_present=False,
           allow_main_commit=False):
    return command_policy.evaluate_bash(
        cmd,
        in_worktree=in_worktree,
        policy=POLICY,
        background=background,
        index_lock_present=index_lock_present,
        allow_main_commit=allow_main_commit,
    )


class TestAllowed(unittest.TestCase):
    def test_wt_tasks(self):
        for cmd in ("mise run wt:new feat-x", "mise run wt:rebase",
                    "mise run wt:verify", "mise run wt:list", "mise run wt:unlock",
                    "mise run gate:fix", "mise run pre-commit", "mise run pre-stop",
                    "mise run check"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_retired_wt_gate_denied(self):
        # spec-prune-gate-vocabulary retired the `wt:gate` task (its warn-behind step
        # folded into `pre-commit`). It is off the allowlist now, so the engine must
        # DENY it — the sanctioned checks are `mise run pre-commit` / `mise run pre-stop`.
        self.assertFalse(decide("mise run wt:gate").allowed)
        self.assertFalse(decide("mise wt:gate").allowed)

    def test_wt_unlock_allowed_but_bare_rm_denied(self):
        # Spec 0048: stale-lock recovery is granted through the named, no-arg task,
        # NOT by widening the arbitrary-delete capability. `mise run wt:unlock` is
        # allowed; a bare `rm` of the same lock stays denied (default-deny).
        self.assertTrue(decide("mise run wt:unlock").allowed)
        self.assertFalse(decide("rm .git/worktrees/feat-x/index.lock", in_worktree=True).allowed)

    def test_dep_add_allowed(self):
        # The sanctioned path to `pnpm add` (bare pnpm is denied) — the task wraps
        # `pnpm --filter web add -E`. Trailing pkg names / -D flags ride along.
        for cmd in ("mise run dep:add vitest", "mise run dep:add -D @types/node",
                    "mise run dep:add msw@2.14.6"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_mise_meta(self):
        for cmd in ("mise tasks", "mise trust", "mise install", "mise --version", "mise"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_read_only(self):
        for cmd in ("ls -la", "cat README.md", "rg pattern src", "grep -n foo file",
                    "find . -name '*.py'", "pwd", "echo hello", "head -5 file", "wc -l f"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_git_read_only(self):
        for cmd in ("git status", "git log --oneline -5", "git diff", "git show HEAD",
                    "git rev-parse HEAD", "git worktree list", "git branch",
                    "git -C /tmp status", "git merge-base HEAD main"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_git_write_in_worktree(self):
        for cmd in ("git add .", "git add -A", "git commit -m 'x'", "git commit --amend",
                    "git rebase main", "git rebase --continue", "git stash",
                    "git restore file", "git switch -c branch", "git fetch origin"):
            self.assertTrue(decide(cmd, in_worktree=True).allowed, cmd)

    def test_force_with_lease_allowed_in_worktree(self):
        self.assertTrue(decide("git push --force-with-lease", in_worktree=True).allowed)

    def test_scripts(self):
        for cmd in ("python3 scripts/specs.py --all", "scripts/specs.py list",
                    "./scripts/guards/guard_commit_location.py",
                    "python3 scripts/guards/guard_commit_location.py", "bash -n scripts/anything.sh"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_compound_all_allowed(self):
        self.assertTrue(decide("git add . && git commit -m 'x'", in_worktree=True).allowed)
        self.assertTrue(decide("git status | grep modified").allowed)

    def test_env_prefix(self):
        self.assertTrue(decide("RUST_LOG=debug git status").allowed)

    def test_cargo_loop_allowed_anywhere(self):
        # The per-crate iteration loop — allowed in main OR a worktree (touches only
        # the gitignored target/ cache). `cargo fmt --check` is read-only; the `--`
        # passthrough form (mise's format:check) must also pass.
        for cmd in ("cargo check", "cargo check -p agenticd-core", "cargo clippy",
                    "cargo nextest run --workspace", "cargo nextest run -p agenticd-server",
                    "cargo build -p agenticd", "cargo tree", "cargo metadata",
                    "cargo fmt --check", "cargo fmt --all -- --check",
                    "cargo +nightly check"):
            self.assertTrue(decide(cmd).allowed, cmd)
            self.assertTrue(decide(cmd, in_worktree=True).allowed, cmd)

    def test_pnpm_read_only_allowed(self):
        for cmd in ("pnpm list", "pnpm ls --depth 0", "pnpm why react",
                    "pnpm outdated", "pnpm licenses list"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_expanded_read_only_inspection(self):
        # Harmless read-only inspection programs widened so agents can script
        # without tripping default-deny. None mutate the repo or reach the network.
        for cmd in ("read -r line", "[ -f file ]", "expr 1 + 2", ": noop",
                    "tac file", "rev file", "fold -w80 file", "base64 README.md",
                    "sha256sum file", "md5sum file", "cksum file", "strings bin",
                    "xxd file", "od -c file", "df -h", "du -sh .", "ps aux",
                    "pgrep -f agenticd", "printenv PATH", "nproc", "hostname",
                    "uptime", "sleep 1", "cal 2026", "tput cols", "whereis python3",
                    "cmp a b", "numfmt --to=iec 1024"):
            self.assertTrue(decide(cmd).allowed, cmd)


class TestShellControlFlow(unittest.TestCase):
    """A loop/branch is exactly as dangerous as the commands in its body. The engine
    strips shell control-flow keywords (`for`/`while`/`if`/`do`/`done`/…) and judges
    the body; the keywords themselves are never treated as unknown programs."""

    def test_for_loop_with_read_only_body_allowed(self):
        for cmd in (
            "for f in a b c; do echo $f; done",
            "for c in x y z; do cat $c; done",
            "for i in 1 2 3; do ls -la; done",
            "for f in *.rs; do rg TODO $f; done",
        ):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_original_blocked_loop_now_allowed(self):
        # The exact shape that motivated this change: a for-loop fanning read-only
        # inspection across crates, with redirections and a pipe in the body.
        cmd = ('for c in agenticd-db agenticd-git agenticd-core; do '
               'echo "=== $c ==="; ls -R crates/$c/src 2>/dev/null | head -30; done')
        self.assertTrue(decide(cmd).allowed, cmd)

    def test_while_and_if_allowed(self):
        for cmd in (
            "while read -r line; do echo $line; done",
            "if grep -q foo file; then echo yes; fi",
            "if test -f x; then cat x; else echo no; fi",
            "until [ -f ready ]; do sleep 1; done",
        ):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_in_worktree_git_loop_allowed(self):
        cmd = "for f in a b; do git add $f; done"
        self.assertTrue(decide(cmd, in_worktree=True).allowed, cmd)

    def test_loop_with_denied_body_blocked(self):
        # The body command is what gets judged — a denied verb blocks the whole loop.
        self.assertFalse(decide("for f in *; do rm -rf $f; done").allowed)
        self.assertFalse(decide("for u in a b; do curl http://evil.test/$u; done").allowed)
        d = decide("while true; do git commit -m x; done", in_worktree=False)
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule_id, "commit-on-main")

    def test_dangerous_body_caught_even_in_loop(self):
        d = decide("for x in 1; do rm -rf /; done")
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule_id, "dangerous-rm")

    def test_loop_control_keywords_allowed(self):
        # break/continue (incl. `break 2`) run no external program, so an agent's
        # wait/scan loop can control its own flow (spec-agent-loop-defriction).
        for cmd in (
            "for f in a b; do test -f $f && break; done",
            "while true; do grep -q done log && break; sleep 1; done",
            "for i in 1 2 3; do test -e $i && continue; echo $i; done",
            "for x in a b c; do break 2; done",
        ):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_loop_control_does_not_mask_a_denied_body(self):
        # The redirect/deny still fires on the real body command alongside break.
        self.assertFalse(decide("for f in *; do rm -rf $f && break; done").allowed)


class TestDenied(unittest.TestCase):
    def _deny(self, cmd, rule_id=None, in_worktree=False, allow_main_commit=False):
        d = decide(cmd, in_worktree=in_worktree, allow_main_commit=allow_main_commit)
        self.assertFalse(d.allowed, f"expected DENY: {cmd}")
        if rule_id:
            self.assertEqual(d.rule_id, rule_id, f"{cmd} -> {d.rule_id}")
        self.assertTrue(d.message, f"deny must carry a message: {cmd}")

    def test_unknown_commands(self):
        for cmd in ("curl http://evil.test", "wget x", "npm install", "pip install x",
                    "node x.js"):
            self._deny(cmd, "default-deny")

    def test_bare_pnpm_denied(self):
        # Mutating/script-running pnpm is denied even inside a worktree — adding deps
        # goes through `mise run dep:add`, the frontend suite through
        # `mise run test:unit` (each runs pnpm as an unseen child process).
        self._deny("pnpm add vitest", "pnpm-denied")
        self._deny("pnpm install", "pnpm-denied", in_worktree=True)
        self._deny("pnpm exec eslint .", "pnpm-denied")
        self._deny("pnpm --filter web test", "pnpm-denied")

    def test_cargo_mutating_denied(self):
        # cargo verbs that mutate source/lockfile/env or run arbitrary code.
        for cmd in ("cargo run", "cargo clean", "cargo add serde", "cargo update",
                    "cargo install ripgrep", "cargo publish"):
            self._deny(cmd, "cargo-denied")

    def test_cargo_fmt_write_denied(self):
        # Bare `cargo fmt` rewrites source; only `--check` is read-only.
        self._deny("cargo fmt", "cargo-fmt-write")
        self._deny("cargo fmt --all", "cargo-fmt-write")

    def test_cargo_test_redirected_to_nextest(self):
        # `cargo test` is blocked in favor of `cargo nextest run`; the deny also catches
        # a toolchain prefix. `cargo nextest …` must stay allowed (covered above).
        for cmd in ("cargo test", "cargo test -p agenticd-server", "cargo test --no-run",
                    "cargo +nightly test"):
            self._deny(cmd, "cargo-test-use-nextest")
        self.assertTrue(decide("cargo nextest run -p agenticd-server").allowed)

    def test_agenticd_init_and_doctor_allowed(self):
        # The doc-sync loop (spec-docs-source-of-truth-inversion): agents edit bundle
        # templates and run `agenticd init`. init + doctor + the build forms ride along.
        for cmd in ("agenticd init", "agenticd init .", "agenticd init --dry-run",
                    "./target/debug/agenticd init", "./target/release/agenticd init .",
                    "target/debug/agenticd init", "agenticd doctor",
                    "agenticd --version", "agenticd"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_agenticd_unknown_subcommand_denied(self):
        # Mutating / fleet subcommands stay the human's call.
        for cmd in ("agenticd serve", "agenticd upgrade --all",
                    "./target/debug/agenticd export-openapi"):
            self._deny(cmd, "agenticd-denied")

    def test_commit_on_main(self):
        self._deny("git commit -m 'x'", "commit-on-main", in_worktree=False)

    def test_commit_in_worktree_allowed(self):
        self.assertTrue(decide("git commit -m 'x'", in_worktree=True).allowed)

    def test_commit_on_main_hatch_allows(self):
        # AGENTIC_ALLOW_MAIN_COMMIT=1 (plumbed as allow_main_commit) opens the
        # commit-on-main deny — the /change direct-to-main path, mirroring the edit
        # guard's AGENTIC_ALLOW_MAIN_EDIT hatch.
        # evaluate_bash collapses a fully-allowed command to "all-subcommands-allowed";
        # the per-subcommand "git-commit-main-hatch" id is only the internal allow reason.
        self.assertTrue(decide("git commit -m 'x'", in_worktree=False,
                               allow_main_commit=True).allowed)

    def test_commit_on_main_hatch_keeps_no_verify_block(self):
        # The hatch widens WHERE you may commit; it never re-enables --no-verify.
        self._deny("git commit -n -m 'x'", "no-verify",
                   in_worktree=False, allow_main_commit=True)

    def test_add_on_main_hatch_allows(self):
        # A direct-to-main commit must stage first, and `git commit -a` skips a NEW
        # untracked file — so the same AGENTIC_ALLOW_MAIN_COMMIT hatch opens `git add`
        # on main (the /change fast path's `git add <files>` step). The compound
        # add+commit, the actual fast-path flow, is allowed as a whole; without the
        # hatch a main `git add` stays denied (see test_git_mutate_on_main).
        self.assertTrue(decide("git add change.txt", in_worktree=False,
                               allow_main_commit=True).allowed)
        self.assertTrue(decide("git add .", in_worktree=False,
                               allow_main_commit=True).allowed)
        self.assertTrue(decide("git add foo && git commit -m 'x'", in_worktree=False,
                               allow_main_commit=True).allowed)

    def test_force_push(self):
        self._deny("git push --force", "force-push")
        self._deny("git push -f origin main", "force-push")
        self._deny("git push --force", "force-push", in_worktree=True)  # force beats worktree

    def test_reset_hard(self):
        self._deny("git reset --hard main", "reset-hard")
        self._deny("git reset --hard HEAD~1", "reset-hard", in_worktree=True)

    def test_no_verify(self):
        self._deny("git commit --no-verify -m 'x'", "no-verify", in_worktree=True)
        self._deny("git commit -n -m 'x'", "no-verify", in_worktree=True)

    def test_rebase_onto_remote_redirected(self):
        # Local-first repo: rebasing onto origin/… is wrong; redirect to wt:rebase
        # (spec-agent-loop-defriction). Fires even inside a worktree and through a fetch chain.
        self._deny("git rebase origin/main", "rebase-onto-remote", in_worktree=True)
        self._deny("git fetch origin && git rebase origin/main", "rebase-onto-remote", in_worktree=True)
        self.assertIn("wt:rebase", decide("git rebase origin/main", in_worktree=True).message)

    def test_local_and_in_progress_rebase_still_allowed(self):
        # Only a REMOTE target is redirected — local rebase + conflict resolution stay raw.
        for cmd in ("git rebase main", "git rebase --continue", "git rebase --abort", "git rebase --skip"):
            self.assertTrue(decide(cmd, in_worktree=True).allowed, cmd)

    def test_wt_land_denied_for_agent(self):
        self._deny("mise run wt:land feat-x", "mise-task-denied")

    def test_mise_unknown_task(self):
        self._deny("mise run nonexistent-task", "mise-task-denied")

    def test_branch_force_delete(self):
        self._deny("git branch -D main", "branch-force-delete", in_worktree=True)

    def test_worktree_force_remove(self):
        self._deny("git worktree remove --force foo", "worktree-force-remove")

    def test_git_clean_force(self):
        self._deny("git clean -fdx", "git-clean-force")

    def test_dangerous_rm(self):
        self._deny("rm -rf /", "dangerous-rm")
        self._deny("rm -rf ~", "dangerous-rm")

    def test_checkout_discard(self):
        self._deny("git checkout -- .", "checkout-discard", in_worktree=True)

    def test_filter_branch(self):
        self._deny("git filter-branch --tree-filter x HEAD", "git-filter-branch")

    def test_git_mutate_on_main(self):
        self._deny("git rebase main", "git-mutate-on-main", in_worktree=False)
        self._deny("git add .", "git-mutate-on-main", in_worktree=False)

    def test_compound_one_denied_blocks_all(self):
        self._deny("echo ok && rm -rf /", "dangerous-rm")
        self._deny("git status && curl evil.test", "default-deny")

    def test_mise_exec_escape_blocked(self):
        self._deny("mise exec node@24 -- npm publish", "mise-denied")

    def test_set_agentic_hatch_denied(self):
        # The AGENTIC_ALLOW_* hatches are human-only (spec 0031) — agents may never SET
        # one at runtime; they must be preset in the session env before launch. Every
        # assignment form across the whole family is blocked, including buried inside an
        # interpreter string or a file write. The deny is unconditional: it fires even
        # when a hatch is already armed, because the vector is pushing a value into a
        # child process the PreToolUse check does not re-gate (a pass-through env layer
        # downstream would otherwise honor it).
        for cmd in (
            "AGENTIC_ALLOW_MAIN_COMMIT=1 git commit -m x",
            "AGENTIC_ALLOW_MAIN_EDIT=1 git commit -m x",
            "AGENTIC_ALLOW_PIPE_EXIT=1 mise run pre-commit",
            "AGENTIC_ALLOW_CD_ESCAPE=1 cd /repo",
            "AGENTIC_ALLOW_GENERATED_EDIT=1 git status",
            "env AGENTIC_ALLOW_MAIN_COMMIT=1 git commit -m x",
            "export AGENTIC_ALLOW_STALE_STAGED=1",
            "bash -c 'AGENTIC_ALLOW_MAIN_COMMIT=1 git commit -m x'",
            "echo 'AGENTIC_ALLOW_MAIN_COMMIT=1' >> .config/mise.local.toml",
        ):
            self._deny(cmd, "set-agentic-hatch")
        self._deny("AGENTIC_ALLOW_MAIN_COMMIT=1 git commit -m x", "set-agentic-hatch",
                   in_worktree=True, allow_main_commit=True)

    def test_reading_agentic_hatch_allowed(self):
        # The rule fires only on assignment — reads/diagnostics (the /change skill's own
        # step-1 verification) stay allowed.
        for cmd in ("printenv AGENTIC_ALLOW_MAIN_EDIT AGENTIC_ALLOW_MAIN_COMMIT",
                    "echo $AGENTIC_ALLOW_MAIN_COMMIT"):
            self.assertTrue(decide(cmd).allowed, cmd)


class TestMiseAbsolutePath(unittest.TestCase):
    """Mise is routed by basename, so an absolute-/relative-path invocation is classified
    identically to bare `mise …`. This is the agent's escape when bare `mise` resolves to
    the `mise activate` shell FUNCTION (undefined in the non-interactive Bash) and dies
    with `command not found`: resolve the binary (`command -v mise`) and call it by path.
    Allow AND deny parity must hold — the path must never become a policy bypass."""

    ABS = "/home/dev/.local/bin/mise"

    def test_absolute_path_runs_allowed_like_bare(self):
        for cmd in (f"{self.ABS} run wt:new feat-x", f"{self.ABS} run pre-commit",
                    f"{self.ABS} run pre-stop", "~/.local/bin/mise run check",
                    "/usr/local/bin/mise tasks", "./bin/mise run wt:rebase",
                    f"{self.ABS} --version"):
            self.assertTrue(decide(cmd, in_worktree=True).allowed, cmd)

    def test_absolute_path_is_not_a_bypass(self):
        # The classifier + deny_rules must still fire on the path form: unknown task,
        # the human-only e2e suite, human-only land, and the `exec` escape hatch.
        self.assertEqual(decide(f"{self.ABS} run nonexistent-task").rule_id, "mise-task-denied")
        self.assertEqual(decide(f"{self.ABS} run wt:land feat-x").rule_id, "mise-task-denied")
        self.assertEqual(decide(f"{self.ABS} run test:e2e").rule_id, "full-e2e-human-only")
        self.assertEqual(decide(f"{self.ABS} exec node@24 -- npm publish").rule_id, "mise-denied")

    def test_absolute_path_git_mutating_task_seen_by_concurrency_guard(self):
        # `/…/mise run wt:rebase` mutates git; the background / index-lock guard must see
        # it exactly like bare `mise run wt:rebase`, or two git ops could overlap.
        self.assertTrue(command_policy.command_has_git_mutation(f"{self.ABS} run wt:rebase", POLICY))
        self.assertEqual(
            decide(f"{self.ABS} run wt:rebase", in_worktree=True, background=True).rule_id,
            "background-git-mutation")
        self.assertEqual(
            decide(f"{self.ABS} run wt:rebase", in_worktree=True, index_lock_present=True).rule_id,
            "index-lock-held")


class TestE2eGatingChangedSpecs(unittest.TestCase):
    """The hand-off gate `pre-stop` is in the universal baseline floor. The
    changed-files e2e tasks (`test:e2e:changed` family) are stack-specific — a
    downstream repo with e2e EXTENDS the floor with them, so they are NOT allowed
    by a fresh install. The full suite (`test:e2e`, `ci-full`, `pre-push`) is
    human-only — it mines the CPU — and the shipped DENY rule `full-e2e-human-only`
    enforces that while its lookahead spares the changed variants."""

    def test_agent_gate_allowed(self):
        # `pre-stop` is the agent hand-off gate and lives in the minimal floor.
        for cmd in ("mise run pre-stop", "mise pre-stop"):
            self.assertTrue(decide(cmd).allowed, cmd)

    def test_full_suite_human_only_denied(self):
        for cmd in ("mise run test:e2e", "mise run ci-full", "mise run pre-push",
                    "mise test:e2e", "mise ci-full", "mise pre-push", "mise run test:e2e --headed"):
            d = decide(cmd)
            self.assertFalse(d.allowed, f"expected DENY: {cmd}")
            self.assertEqual(d.rule_id, "full-e2e-human-only", f"{cmd} -> {d.rule_id}")
            self.assertTrue(d.message)

    def test_deny_regex_does_not_catch_the_agent_variants(self):
        # The lookahead must spare :changed / :changed:dirty / :install (a `\b` after
        # "e2e" would not). These are stack-specific tasks, absent from the minimal
        # floor, so assert the human-only DENY rule does not CLAIM them — not that a
        # fresh floor allows them (a downstream repo's allowlist extension does that).
        for cmd in ("mise run test:e2e:changed", "mise run test:e2e:changed:dirty",
                    "mise run test:e2e:install"):
            self.assertNotEqual(decide(cmd).rule_id, "full-e2e-human-only", cmd)


class TestWorktreeDetection(unittest.TestCase):
    def test_is_worktree_path(self):
        self.assertTrue(command_policy.is_worktree_path(WT))
        self.assertFalse(command_policy.is_worktree_path(MAIN))
        self.assertFalse(command_policy.is_worktree_path(""))

    def test_effective_in_worktree_from_cwd(self):
        self.assertTrue(command_policy.effective_in_worktree("git commit -m x", WT))
        self.assertFalse(command_policy.effective_in_worktree("git commit -m x", MAIN))

    def test_effective_in_worktree_via_leading_cd(self):
        cmd = "cd .claude/worktrees/feat-x && git commit -m x"
        self.assertTrue(command_policy.effective_in_worktree(cmd, MAIN))


class TestWorktreeEscapeGuard(unittest.TestCase):
    """A hop into the repo OUTSIDE the worktree (main checkout / sibling) is blocked;
    a hop to a truly-external path (/tmp, ~) is fine. Covers `cd` and `git -C`."""

    def _esc(self, cmd, cwd=WT):
        return command_policy.worktree_escape_decision(cmd, cwd)

    def test_cd_to_main_root_blocked(self):
        d = self._esc(f"cd {MAIN} && rg foo crates/")
        self.assertIsNotNone(d)
        self.assertEqual(d.rule_id, "cd-escapes-worktree")

    def test_cd_to_main_subdir_blocked(self):
        self.assertIsNotNone(self._esc(f"cd {MAIN}/crates && rg foo"))

    def test_cd_to_main_via_relative_dotdot_blocked(self):
        # `.claude/worktrees/feat-x` → three `..` reach the main root.
        self.assertIsNotNone(self._esc("cd ../../.. && git status"))

    def test_bare_command_no_cd_allowed(self):
        self.assertIsNone(self._esc("git status"))

    def test_cd_into_worktree_subdir_allowed(self):
        self.assertIsNone(self._esc("cd crates/agenticd && cargo check"))

    def test_cd_back_to_worktree_root_allowed(self):
        self.assertIsNone(self._esc(f"cd {WT} && git status"))

    def test_chained_cd_staying_inside_allowed(self):
        # cd sub then `..` lands back at the worktree root — never escapes.
        self.assertIsNone(self._esc("cd crates && cd .. && git status"))

    def test_cd_to_external_tmp_allowed(self):
        # /tmp is outside the repo entirely — no checkout confusion, so allowed.
        self.assertIsNone(self._esc("cd /tmp && ls"))

    def test_cd_to_home_allowed(self):
        self.assertIsNone(self._esc("cd ~ && ls"))
        self.assertIsNone(self._esc("cd && ls"))

    def test_cd_to_other_project_allowed(self):
        self.assertIsNone(self._esc("cd /home/u/other-project && ls"))

    def test_cd_to_sibling_worktree_blocked(self):
        sibling = MAIN + "/.claude/worktrees/feat-y"
        self.assertIsNotNone(self._esc(f"cd {sibling} && git status"))

    def test_git_dash_c_to_main_blocked(self):
        d = self._esc(f"git -C {MAIN} status")
        self.assertIsNotNone(d)
        self.assertEqual(d.rule_id, "git-c-escapes-worktree")

    def test_git_dash_c_to_worktree_subdir_allowed(self):
        self.assertIsNone(self._esc("git -C crates/agenticd status"))

    def test_git_dash_c_without_escape_allowed(self):
        self.assertIsNone(self._esc("git status"))

    def test_not_in_worktree_is_unenforced(self):
        # cwd is the main checkout — this guard is about leaving a WORKTREE.
        self.assertIsNone(self._esc(f"cd {MAIN} && git status", cwd=MAIN))


class TestPipelineExitStatusGuard(unittest.TestCase):
    """Reading `$?` right after a pipeline reports the pipe's LAST stage, not the
    command you meant — the silent-commit-failure trap. Flagged; PIPESTATUS is the fix."""

    def _pipe(self, cmd):
        return command_policy.pipeline_exit_status_decision(cmd)

    def test_commit_piped_to_tail_then_echo_exit_blocked(self):
        d = self._pipe('git commit -q -m x 2>&1 | tail -4; echo "EXIT=$?"')
        self.assertIsNotNone(d)
        self.assertEqual(d.rule_id, "pipeline-masks-exit-status")

    def test_pipe_then_bare_echo_dollar_question_blocked(self):
        self.assertIsNotNone(self._pipe("make build | grep error; echo $?"))

    def test_pipe_then_var_assignment_blocked(self):
        self.assertIsNotNone(self._pipe("cmd | head; EXIT=$?"))

    def test_pipe_then_conditional_test_blocked(self):
        self.assertIsNotNone(self._pipe("cmd | tail; if [ $? -ne 0 ]; then echo bad; fi"))

    def test_pipe_then_exit_via_and_operator_blocked(self):
        # `&&` after a pipe still reads the pipe's (tail's) status into `$?`.
        self.assertIsNotNone(self._pipe("cmd | tail -4 && echo $?"))

    def test_subshell_pipe_then_echo_exit_blocked(self):
        # `( cmd | tail )` returns tail's status; reading `$?` after is the same trap.
        self.assertIsNotNone(self._pipe("( cmd | tail ); echo $?"))

    def test_pipestatus_is_the_fix_not_flagged(self):
        self.assertIsNone(self._pipe('cmd | tail; echo "EXIT=${PIPESTATUS[0]}"'))

    def test_dollar_question_without_preceding_pipe_allowed(self):
        # Correct usage: `$?` after a non-pipeline command is the command's own status.
        self.assertIsNone(self._pipe("git commit -m x; echo $?"))
        self.assertIsNone(self._pipe("false; echo $?"))

    def test_pipe_with_no_exit_read_allowed(self):
        self.assertIsNone(self._pipe("cmd | tail -4"))
        self.assertIsNone(self._pipe("git log | head; echo done"))

    def test_intervening_nonpipe_statement_clears_the_flag(self):
        # `$?` here refers to `echo done`, not the pipe — not the trap.
        self.assertIsNone(self._pipe("cmd | tail; echo done; echo $?"))

    def test_dollar_question_before_pipe_in_same_statement_allowed(self):
        # `echo $? | tail` reads a *prior* command's status; not the described bug.
        self.assertIsNone(self._pipe("echo $? | tail"))

    def test_heredoc_apostrophe_does_not_disarm_guard(self):
        # A heredoc body with a lone apostrophe used to break shlex → the guard failed
        # OPEN. Stripping the heredoc first keeps the pipeline-then-`$?` trap visible.
        cmd = (
            "cat > /tmp/m <<'EOF'\n"
            "note: don't trust the tail exit\n"
            "EOF\n"
            "git commit -F /tmp/m 2>&1 | tail; echo $?"
        )
        self.assertIsNotNone(self._pipe(cmd))

    def test_unparsable_input_is_unenforced(self):
        # Unbalanced quote → fail open (this guard only ever ADDS protection).
        self.assertIsNone(self._pipe('cmd | tail; echo "$?'))

    def _hook(self, command, env=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": WT}
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env if env is not None else {**os.environ},
        )
        return proc.returncode, proc.stderr

    def test_blocked_end_to_end_via_hook(self):
        code, err = self._hook('git commit -q -m x 2>&1 | tail -4; echo "EXIT=$?"')
        self.assertEqual(code, 2, err)
        self.assertIn("PIPESTATUS", err)

    def test_hatch_env_allows_via_hook(self):
        env = {**os.environ, "AGENTIC_ALLOW_PIPE_EXIT": "1"}
        # The pipe-exit guard is bypassed; a clean run means it did not fire (the
        # assertion under test). `git status | cat; echo $?` keeps both stages on the
        # allowlist so the only thing that could block is the guard itself.
        _, err = self._hook("git status | cat; echo $?", env=env)
        self.assertNotIn("PIPESTATUS", err)


class TestPipelineMasksCommandGuard(unittest.TestCase):
    """A `git commit` / `mise run` piped into another command masks its exit even when
    `$?` is never read — the agent eyeballs the trimmed output past a red gate / rejected
    commit. Deterministic block on a gate/commit as a non-final pipeline stage; read-only
    `… | tail` and a bare commit/gate are untouched (spec-no-pipe-gate-commands-prompt-rule)."""

    def _pipe(self, cmd):
        return command_policy.pipeline_masks_command_decision(cmd)

    def test_commit_piped_to_tail_blocked(self):
        d = self._pipe("git commit -m x | tail -4")
        self.assertIsNotNone(d)
        self.assertEqual(d.rule_id, "pipeline-masks-command")

    def test_commit_with_redirect_piped_blocked(self):
        self.assertIsNotNone(self._pipe("git commit -q -m x 2>&1 | tail"))

    def test_gate_piped_to_tail_blocked(self):
        d = self._pipe("mise run pre-stop 2>&1 | tail -40")
        self.assertIsNotNone(d)
        self.assertEqual(d.rule_id, "pipeline-masks-command")

    def test_gate_piped_to_grep_blocked(self):
        self.assertIsNotNone(self._pipe("mise run check | grep -i error"))

    def test_commit_with_global_flag_still_detected(self):
        # `git -C <dir> commit | tail` — the subcommand is found past global flags.
        self.assertIsNotNone(self._pipe("git -C . commit -m x | head"))

    def test_second_statement_pipe_blocked(self):
        # First statement is read-only; the masked commit is in the second statement.
        self.assertIsNotNone(self._pipe("git status | cat && git commit -m x | tail"))

    # ── allowed: bare, read-only, or commit/gate as the FINAL stage ──────────────
    def test_bare_commit_allowed(self):
        self.assertIsNone(self._pipe("git commit -m x"))

    def test_bare_gate_allowed(self):
        self.assertIsNone(self._pipe("mise run pre-stop"))

    def test_readonly_pipe_allowed(self):
        self.assertIsNone(self._pipe("git log | tail -4"))
        self.assertIsNone(self._pipe("git diff | head"))
        self.assertIsNone(self._pipe("cat file | grep x | tail"))

    def test_commit_reading_stdin_as_final_stage_allowed(self):
        # `… | git commit -F -` reads the message from stdin; commit is the LAST stage,
        # so its exit is NOT masked — must not be flagged (would break a legit pattern).
        self.assertIsNone(self._pipe("cat /tmp/msg | git commit -F -"))

    def test_gate_as_final_stage_allowed(self):
        self.assertIsNone(self._pipe("echo x | mise run pre-commit"))

    def test_mise_readonly_subcommand_piped_allowed(self):
        # `mise tasks` is not a `run`; piping it carries no exit you act on.
        self.assertIsNone(self._pipe("mise tasks | grep wt"))

    def test_unparsable_input_is_unenforced(self):
        # A lone `|` *inside* an unterminated quote is the message text, not a real pipe —
        # we can't parse it, so we stay hands-off rather than risk a false block.
        self.assertIsNone(self._pipe('git commit -m "x | tail'))

    # ── heredoc commit messages (the real-world masking the agent hit) ────────────
    def test_heredoc_commit_with_apostrophe_piped_blocked(self):
        # The footgun a real run masked: `git commit -F … | tail` with a `||` heredoc
        # fallback whose prose body carries a lone apostrophe (`don't`). The apostrophe
        # used to break shlex → the guard failed OPEN and the piped commit slipped past.
        # Stripping the heredoc body first lets the precise parser see the real pipe.
        cmd = (
            "git commit -F /tmp/m 2>&1 | tail -15 || (cat > /tmp/m <<'EOF'\n"
            "fix: don't double-count the events\n"
            "EOF\n"
            "git commit -F /tmp/m 2>&1 | tail -15)"
        )
        d = self._pipe(cmd)
        self.assertIsNotNone(d)
        self.assertEqual(d.rule_id, "pipeline-masks-command")

    def test_heredoc_without_piped_commit_allowed(self):
        # A heredoc whose body has an apostrophe but no piped commit/gate stays allowed —
        # stripping the heredoc must not invent a footgun where there is none.
        cmd = (
            "git commit -F /tmp/m && (cat > /tmp/m <<'EOF'\n"
            "fix: it's fine\n"
            "EOF\n"
            ")"
        )
        self.assertIsNone(self._pipe(cmd))

    # ── end-to-end via the hook ──────────────────────────────────────────────────
    def _hook(self, command, env=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": WT}
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env if env is not None else {**os.environ},
        )
        return proc.returncode, proc.stderr

    def test_blocked_end_to_end_via_hook(self):
        code, err = self._hook("git commit -q -m x 2>&1 | tail -4")
        self.assertEqual(code, 2, err)
        self.assertIn("masks its exit", err)

    def test_hatch_env_allows_via_hook(self):
        env = {**os.environ, "AGENTIC_ALLOW_PIPE_EXIT": "1"}
        _, err = self._hook("git commit -q -m x | tail", env=env)
        self.assertNotIn("masks its exit", err)


class TestMainTreeEditGuard(unittest.TestCase):
    """The file-tool analogue of no-commit-on-main (R7)."""

    def test_main_root_from_cwd(self):
        self.assertEqual(command_policy.main_root_from_cwd(WT), MAIN)
        self.assertEqual(command_policy.main_root_from_cwd(MAIN), MAIN)
        self.assertEqual(command_policy.main_root_from_cwd(MAIN + "/"), MAIN)

    def test_edit_in_main_tree_is_blocked(self):
        # cwd in the worktree, but the edit targets a main-checkout path.
        self.assertTrue(command_policy.is_main_tree_edit(MAIN + "/src/foo.rs", WT))
        # cwd is the main checkout (agent never entered a worktree).
        self.assertTrue(command_policy.is_main_tree_edit(MAIN + "/CLAUDE.md", MAIN))

    def test_edit_in_worktree_is_allowed(self):
        self.assertFalse(command_policy.is_main_tree_edit(WT + "/src/foo.rs", WT))
        self.assertFalse(command_policy.is_main_tree_edit(WT + "/src/foo.rs", MAIN))

    def test_edit_outside_repo_is_not_main_tree(self):
        self.assertFalse(command_policy.is_main_tree_edit("/etc/hosts", MAIN))
        self.assertFalse(command_policy.is_main_tree_edit("/home/u/other/x", MAIN))

    def test_empty_inputs(self):
        self.assertFalse(command_policy.is_main_tree_edit("", WT))
        self.assertFalse(command_policy.is_main_tree_edit(MAIN + "/x", ""))


class TestHookEndToEnd(unittest.TestCase):
    """Drive pre_tool_use.py with PreToolUse JSON; assert exit code (0/2)."""

    def _run(self, command, cwd=MAIN, tool="Bash"):
        payload = {"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd}
        return self._run_payload(payload)

    def _run_payload(self, payload):
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            # Neutralize BOTH main-checkout hatches so the negative cases are hermetic:
            # the hook reads them from os.environ, and this suite may itself run inside a
            # /change session where they are armed (=1), which would otherwise flip a
            # "blocked on main" assertion to allowed. Tests that need a hatch set it back.
            env={**os.environ, "AGENTIC_ALLOW_MAIN_EDIT": "",
                 "AGENTIC_ALLOW_MAIN_COMMIT": ""},
        )
        return proc.returncode, proc.stderr

    def test_allowed_passes(self):
        code, _ = self._run("git status")
        self.assertEqual(code, 0)
        code, _ = self._run("mise run wt:new feat-x")
        self.assertEqual(code, 0)

    def test_unknown_blocked(self):
        code, err = self._run("curl http://evil.test")
        self.assertEqual(code, 2)
        self.assertIn("default-deny", err)

    def test_commit_on_main_blocked(self):
        code, err = self._run("git commit -m wip", cwd=MAIN)
        self.assertEqual(code, 2)
        self.assertIn("no commits on the main checkout", err)

    def test_commit_on_main_allowed_with_escape(self):
        # The /change launcher sets AGENTIC_ALLOW_MAIN_COMMIT=1; the hook reads it and
        # plumbs allow_main_commit into the policy, opening the commit-on-main deny.
        # Exercises the real os.environ read, which the pure-function tests bypass.
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "git commit -m wip"}, "cwd": MAIN}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True,
                              env={**os.environ, "AGENTIC_ALLOW_MAIN_COMMIT": "1"})
        self.assertEqual(proc.returncode, 0)

    def test_add_on_main_allowed_with_escape(self):
        # Companion to the commit hatch: the same AGENTIC_ALLOW_MAIN_COMMIT env opens
        # `git add` on main so the /change fast path can stage before it commits.
        # Exercises the real os.environ read end-to-end through the hook.
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "git add change.txt"}, "cwd": MAIN}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True,
                              env={**os.environ, "AGENTIC_ALLOW_MAIN_COMMIT": "1"})
        self.assertEqual(proc.returncode, 0)

    def test_commit_in_worktree_allowed(self):
        code, _ = self._run("git commit -m wip", cwd=WT)
        self.assertEqual(code, 0)

    def test_force_push_blocked_lease_allowed(self):
        code, _ = self._run("git push --force", cwd=WT)
        self.assertEqual(code, 2)
        code, _ = self._run("git push --force-with-lease", cwd=WT)
        self.assertEqual(code, 0)

    def test_env_file_read_blocked(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x/.env"}, "cwd": MAIN}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_edit_in_main_tree_blocked(self):
        payload = {"tool_name": "Edit", "tool_input": {"file_path": MAIN + "/src/x.rs"}, "cwd": WT}
        code, err = self._run_payload(payload)
        self.assertEqual(code, 2)
        self.assertIn("no file edits on the main checkout", err)

    def test_write_in_worktree_allowed(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": WT + "/src/x.rs"}, "cwd": WT}
        code, _ = self._run_payload(payload)
        self.assertEqual(code, 0)

    def test_edit_in_main_tree_allowed_with_escape(self):
        payload = {"tool_name": "Edit", "tool_input": {"file_path": MAIN + "/src/x.rs"}, "cwd": WT}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True,
                              env={**os.environ, "AGENTIC_ALLOW_MAIN_EDIT": "1"})
        self.assertEqual(proc.returncode, 0)

    def test_new_doc_write_blocked(self):
        # spec-doc-node-cli: creating a NEW node under a doc dir via Write is blocked and
        # redirected to `mise run docs:new`. (The fake WT path does not exist on disk.)
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": WT + "/docs/specs/2026-06-03-101500-foo.md"}, "cwd": WT}
        code, err = self._run_payload(payload)
        self.assertEqual(code, 2)
        self.assertIn("docs:new", err)

    def test_new_knowledge_doc_write_blocked(self):
        # All four dirs are covered, including the slug-only knowledge dir.
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": WT + "/docs/knowledge/sqlite-fts5.md"}, "cwd": WT}
        code, _ = self._run_payload(payload)
        self.assertEqual(code, 2)

    def test_doc_template_write_allowed(self):
        # Templates (`_`-prefixed scaffolds) are exempt.
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": WT + "/docs/specs/_template.md.tmpl"}, "cwd": WT}
        code, _ = self._run_payload(payload)
        self.assertEqual(code, 0)

    def test_edit_existing_doc_allowed(self):
        # Editing a doc is fine — the Edit tool is never caught by the creation block.
        payload = {"tool_name": "Edit",
                   "tool_input": {"file_path": WT + "/docs/specs/2026-06-03-101500-foo.md"}, "cwd": WT}
        code, _ = self._run_payload(payload)
        self.assertEqual(code, 0)

    def test_write_existing_doc_allowed(self):
        # A Write whose target already exists is a rewrite of an existing doc → allowed.
        with tempfile.TemporaryDirectory() as tmp:
            wt = os.path.join(tmp, ".claude", "worktrees", "feat")
            doc = os.path.join(wt, "docs", "specs", "2026-06-03-101500-foo.md")
            os.makedirs(os.path.dirname(doc))
            with open(doc, "w", encoding="utf-8") as fh:
                fh.write("---\ntype: spec\n---\n")
            payload = {"tool_name": "Write", "tool_input": {"file_path": doc}, "cwd": wt}
            code, _ = self._run_payload(payload)
            self.assertEqual(code, 0)

    def test_cd_out_of_worktree_blocked(self):
        code, err = self._run(f"cd {MAIN} && git status", cwd=WT)
        self.assertEqual(code, 2)
        self.assertIn("points into the main checkout", err)

    def test_cd_out_of_worktree_allowed_with_escape_hatch(self):
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": f"cd {MAIN} && git status"}, "cwd": WT}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True,
                              env={**os.environ, "AGENTIC_ALLOW_CD_ESCAPE": "1"})
        self.assertEqual(proc.returncode, 0)

    def test_bare_command_in_worktree_unaffected(self):
        code, _ = self._run("git status", cwd=WT)
        self.assertEqual(code, 0)

    def test_doc_write_allowed_with_escape(self):
        # The human / migration hatch lets a direct Write of a new node through.
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": WT + "/docs/specs/2026-06-03-101500-foo.md"}, "cwd": WT}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True,
                              env={**os.environ, "AGENTIC_ALLOW_MAIN_EDIT": "",
                                   "AGENTIC_ALLOW_DOC_WRITE": "1"})
        self.assertEqual(proc.returncode, 0)


class TestConcurrencyGuard(unittest.TestCase):
    """spec-git-mutation-concurrency-guard: a git mutation must not overlap another git
    op. Backgrounding is the ONLY overlap vector in the agent's sequential tool loop; a
    held index.lock is the only sign of an in-flight/crashed op. Both deny git MUTATIONS
    only — read-only git and non-git commands are never gated by the guard."""

    def test_backgrounded_git_mutation_denied(self):
        # background beats the in-worktree allow — a backgrounded commit is the exact bug.
        for cmd in ("git commit -m x", "git add .", "git add -A", "git rebase main",
                    "git reset HEAD~1", "git stash", "git push --force-with-lease",
                    "git restore file", "git merge feat", "git cherry-pick abc",
                    "cd .claude/worktrees/feat-x && git commit -m x",
                    "git add . && git commit -m x"):
            d = decide(cmd, in_worktree=True, background=True)
            self.assertFalse(d.allowed, cmd)
            self.assertEqual(d.rule_id, "background-git-mutation", cmd)
            self.assertTrue(d.message, cmd)

    def test_backgrounded_git_mutating_mise_task_denied(self):
        for cmd in ("mise run wt:rebase", "mise run gate:fix", "mise wt:rebase"):
            d = decide(cmd, in_worktree=True, background=True)
            self.assertFalse(d.allowed, cmd)
            self.assertEqual(d.rule_id, "background-git-mutation", cmd)

    def test_backgrounded_readonly_git_allowed(self):
        # Backgrounding inspection is harmless — it writes no shared state.
        for cmd in ("git status", "git log --oneline", "git diff", "git branch",
                    "git worktree list", "git clean -n"):
            self.assertTrue(decide(cmd, in_worktree=True, background=True).allowed, cmd)

    def test_backgrounded_non_git_allowed(self):
        # A build/sleep / non-git-mutating mise task has no shared index for the next
        # command to corrupt, so backgrounding it stays allowed.
        for cmd in ("cargo build -p agenticd", "sleep 60", "cargo nextest run --workspace",
                    "mise run pre-stop"):
            self.assertTrue(decide(cmd, background=True).allowed, cmd)

    def test_index_lock_held_blocks_git_mutation(self):
        d = decide("git commit -m x", in_worktree=True, index_lock_present=True)
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule_id, "index-lock-held")
        self.assertIn("wt:unlock", d.message)
        self.assertFalse(decide("git add .", in_worktree=True, index_lock_present=True).allowed)

    def test_index_lock_held_allows_readonly_and_non_git(self):
        # A held lock must never gate inspection or unrelated commands.
        for cmd in ("git status", "git log", "ls -la", "cargo build"):
            self.assertTrue(decide(cmd, in_worktree=True, index_lock_present=True).allowed, cmd)

    def test_background_takes_precedence_over_lock(self):
        d = decide("git commit -m x", in_worktree=True, background=True, index_lock_present=True)
        self.assertEqual(d.rule_id, "background-git-mutation")

    def test_default_flags_preserve_behavior(self):
        # Both flags default-False (every pre-existing call site): a foreground
        # in-worktree commit is still allowed — the guard is opt-in, driven by the hook.
        self.assertTrue(decide("git commit -m x", in_worktree=True).allowed)

    def test_command_has_git_mutation(self):
        def has(c):
            return command_policy.command_has_git_mutation(c, POLICY)

        for cmd in ("git commit -m x", "git add .", "git rebase main", "git push",
                    "cd .claude/worktrees/feat-x && git commit -m x",
                    "mise run wt:rebase", "echo hi && git reset HEAD~1"):
            self.assertTrue(has(cmd), cmd)
        for cmd in ("git status", "git log -5", "git diff", "git branch", "ls",
                    "cargo build", "mise run pre-stop", "sleep 1",
                    "git clean -n", "git worktree list"):
            self.assertFalse(has(cmd), cmd)


class TestIndexLockResolution(unittest.TestCase):
    """Pure-stdlib gitdir/index.lock resolution (no git binary). Mirrors
    `git rev-parse --git-path index.lock` for normal + linked-worktree layouts (R6).
    The locks here are freshly `_touch`ed (young), so they BLOCK — the age gate that
    distinguishes young-blocks from stale-heals is exercised in `TestIndexLockAgeGate`
    (spec-git-index-lock-agent-contention)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _touch(self, path):
        open(path, "w").close()

    def test_normal_checkout_lock_detected(self):
        root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(root, ".git"))
        self.assertFalse(pre_tool_use._git_index_lock_blocks("git commit -m x", root))
        self._touch(os.path.join(root, ".git", "index.lock"))
        self.assertTrue(pre_tool_use._git_index_lock_blocks("git commit -m x", root))

    def test_linked_worktree_lock_detected(self):
        # The worktree's `.git` is a FILE pointing at main/.git/worktrees/<name> — git's
        # linked-worktree layout, where the per-worktree index.lock lives in the gitdir.
        gitdir = os.path.join(self.tmp, "main", ".git", "worktrees", "feat-x")
        os.makedirs(gitdir)
        wt = os.path.join(self.tmp, "main", ".claude", "worktrees", "feat-x")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as fh:
            fh.write(f"gitdir: {gitdir}\n")
        self.assertFalse(pre_tool_use._git_index_lock_blocks("git commit -m x", wt))
        self._touch(os.path.join(gitdir, "index.lock"))
        self.assertTrue(pre_tool_use._git_index_lock_blocks("git commit -m x", wt))

    def test_subdir_walks_up_to_gitdir(self):
        root = os.path.join(self.tmp, "repo")
        sub = os.path.join(root, "crates", "x", "src")
        os.makedirs(os.path.join(root, ".git"))
        os.makedirs(sub)
        self._touch(os.path.join(root, ".git", "index.lock"))
        self.assertTrue(pre_tool_use._git_index_lock_blocks("git commit -m x", sub))

    def test_cd_prefix_redirects_base(self):
        root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(root, ".git"))
        self._touch(os.path.join(root, ".git", "index.lock"))
        # cwd is elsewhere; the leading `cd <root>` redirects the lock lookup.
        self.assertTrue(
            pre_tool_use._git_index_lock_blocks(f"cd {root} && git commit -m x", self.tmp)
        )

    def test_no_repo_returns_false(self):
        self.assertFalse(pre_tool_use._git_index_lock_blocks("git commit -m x", self.tmp))


class TestIndexLockAgeGate(unittest.TestCase):
    """The age gate (spec-git-index-lock-agent-contention): a YOUNG lock (< 6s, a
    possibly-live op) blocks; a STALE lock (>= 6s, dead) is healed in place and does
    NOT block — so the agent no longer has to hand-run `wt:unlock` for a lock the
    daemon / CLI already auto-heal. Mirrors `lock.rs::lock_age_classifies_stale_and_young`
    from `std` mtime alone (no git binary), using the shared 6s `STALE_LOCK_MIN_AGE`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.root, ".git"))
        self.lock = os.path.join(self.root, ".git", "index.lock")

    def _write_lock(self, age_seconds):
        open(self.lock, "w").close()
        when = time.time() - age_seconds
        os.utime(self.lock, (when, when))

    def test_young_lock_blocks_and_is_left_in_place(self):
        self._write_lock(age_seconds=1)  # < 6s — a possibly-live op
        self.assertTrue(pre_tool_use._git_index_lock_blocks("git commit -m x", self.root))
        self.assertTrue(os.path.isfile(self.lock), "a young lock must NOT be removed")

    def test_stale_lock_is_healed_and_does_not_block(self):
        self._write_lock(age_seconds=30)  # >= 6s — dead, left by a crashed op
        self.assertFalse(pre_tool_use._git_index_lock_blocks("git commit -m x", self.root))
        self.assertFalse(os.path.isfile(self.lock), "a stale lock must be cleared")

    def test_future_mtime_reads_as_young_and_blocks(self):
        # Clock skew: a future mtime yields a negative age (< threshold) ⇒ young ⇒ never
        # yanked from under a possibly-live op (the lock.rs skew rule, ported).
        self._write_lock(age_seconds=-3600)
        self.assertTrue(pre_tool_use._git_index_lock_blocks("git commit -m x", self.root))
        self.assertTrue(os.path.isfile(self.lock))

    def test_remove_failure_fails_open(self):
        # A stale lock whose removal fails (e.g. races another healer) must fail OPEN —
        # allow the op rather than wrongly block; the background-mutation deny is primary.
        self._write_lock(age_seconds=30)
        real_remove = os.remove

        def boom(path, *a, **k):
            if path == self.lock:
                raise OSError("simulated remove failure")
            return real_remove(path, *a, **k)

        os.remove = boom
        try:
            self.assertFalse(
                pre_tool_use._git_index_lock_blocks("git commit -m x", self.root)
            )
        finally:
            os.remove = real_remove


class TestConcurrencyGuardHookEndToEnd(unittest.TestCase):
    """Drive pre_tool_use.py over stdin JSON for the concurrency guard: the backgrounded
    mutation (via `run_in_background`) and the index.lock preflight (real tempdir gitdir,
    no git binary needed since resolution is pure filesystem)."""

    def _run(self, payload):
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            # Neutralize BOTH main-checkout hatches so the negative cases are hermetic:
            # the hook reads them from os.environ, and this suite may itself run inside a
            # /change session where they are armed (=1), which would otherwise flip a
            # "blocked on main" assertion to allowed. Tests that need a hatch set it back.
            env={**os.environ, "AGENTIC_ALLOW_MAIN_EDIT": "",
                 "AGENTIC_ALLOW_MAIN_COMMIT": ""},
        )
        return proc.returncode, proc.stderr

    def test_backgrounded_git_mutation_blocked(self):
        code, err = self._run({"tool_name": "Bash",
                               "tool_input": {"command": "git commit -m x", "run_in_background": True},
                               "cwd": WT})
        self.assertEqual(code, 2)
        self.assertIn("background", err)

    def test_foreground_git_commit_in_worktree_still_allowed(self):
        # Same command WITHOUT the flag → allowed (the guard is opt-in via run_in_background).
        code, _ = self._run({"tool_name": "Bash",
                            "tool_input": {"command": "git commit -m x", "run_in_background": False},
                            "cwd": WT})
        self.assertEqual(code, 0)

    def test_backgrounded_readonly_git_allowed(self):
        code, _ = self._run({"tool_name": "Bash",
                            "tool_input": {"command": "git status", "run_in_background": True},
                            "cwd": WT})
        self.assertEqual(code, 0)

    def test_git_mutation_blocked_when_index_lock_present(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wt = os.path.join(tmp, ".claude", "worktrees", "feat-x")
        os.makedirs(os.path.join(wt, ".git"))
        open(os.path.join(wt, ".git", "index.lock"), "w").close()
        code, err = self._run({"tool_name": "Bash",
                               "tool_input": {"command": "git commit -m x"}, "cwd": wt})
        self.assertEqual(code, 2)
        self.assertIn("index.lock", err)

    def test_git_mutation_allowed_when_no_lock(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wt = os.path.join(tmp, ".claude", "worktrees", "feat-x")
        os.makedirs(os.path.join(wt, ".git"))
        code, _ = self._run({"tool_name": "Bash",
                            "tool_input": {"command": "git commit -m x"}, "cwd": wt})
        self.assertEqual(code, 0)


class TestWorktreeLocalPolicy(unittest.TestCase):
    """A worktree governs its own Bash allowlist data (spec-local-policy-resolution)."""

    def test_worktree_root_from_cwd(self):
        wt = "/home/u/proj/.claude/worktrees/feat-x"
        self.assertEqual(command_policy.worktree_root_from_cwd(wt + "/crates/foo"), wt)
        self.assertEqual(command_policy.worktree_root_from_cwd(wt), wt)
        self.assertIsNone(command_policy.worktree_root_from_cwd("/home/u/proj"))
        self.assertIsNone(command_policy.worktree_root_from_cwd(None))
        # A trailing marker with no slug is not a worktree root.
        self.assertIsNone(command_policy.worktree_root_from_cwd("/home/u/proj/.claude/worktrees/"))

    def test_resolve_allowlist_path_prefers_worktree(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wt = os.path.join(tmp, ".claude", "worktrees", "feat-x")
        policy_dir = os.path.join(wt, ".claude", "hooks", "policy")
        os.makedirs(policy_dir)
        wt_allowlist = os.path.join(policy_dir, "allowlist.toml")
        with open(wt_allowlist, "w") as fh:
            fh.write("schema_version = 1\n")

        # cwd inside the worktree → the worktree's own allowlist.
        self.assertEqual(str(command_policy.resolve_allowlist_path("ls", wt)), wt_allowlist)
        # A leading `cd <worktree> &&` → effective base is the worktree.
        self.assertEqual(
            str(command_policy.resolve_allowlist_path(f"cd {wt} && ls", "/elsewhere")),
            wt_allowlist,
        )
        # Main checkout cwd → the bundled default.
        self.assertEqual(
            command_policy.resolve_allowlist_path("ls", "/home/u/proj"),
            command_policy.DEFAULT_ALLOWLIST,
        )
        # A worktree without an installed allowlist → fall back to the default.
        bare = os.path.join(tmp, ".claude", "worktrees", "bare")
        os.makedirs(bare)
        self.assertEqual(
            command_policy.resolve_allowlist_path("ls", bare),
            command_policy.DEFAULT_ALLOWLIST,
        )

    def test_worktree_policy_grants_a_capability_main_denies(self):
        # The engine-level precondition for the hook's approval gate: the worktree's
        # allowlist grants `curl` while the main allowlist denies it. The hook turns
        # this gap into a one-time `ask` (see test_pre_tool_use's approval test).
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wt = os.path.join(tmp, ".claude", "worktrees", "feat-x")
        policy_dir = os.path.join(wt, ".claude", "hooks", "policy")
        os.makedirs(policy_dir)
        with open(os.path.join(policy_dir, "allowlist.toml"), "w") as fh:
            fh.write(
                'schema_version = 1\n'
                '[fail_mode]\nmutating = "deny"\nread_only = "allow"\n'
                '[allow]\nread_only_commands = ["curl", "ls"]\n'
            )

        wt_policy = command_policy.load_policy(
            command_policy.resolve_allowlist_path("curl example.test", wt)
        )
        self.assertTrue(
            command_policy.evaluate_bash("curl example.test", in_worktree=True, policy=wt_policy).allowed,
            "the worktree allowlist grants the capability",
        )
        self.assertFalse(
            command_policy.evaluate_bash("curl example.test", in_worktree=False, policy=POLICY).allowed,
            "the main checkout's allowlist denies it",
        )


class TestAllowlistEditApproval(unittest.TestCase):
    """The approval is on the *edit* of a worktree's allowlist (a one-time `ask`),
    not per Bash command — so it persists and the worktree allowlist stays in sync
    with main unless approved. The Bash gate then honors the worktree allowlist
    directly (spec-local-policy-resolution, the edit-trap model)."""

    WT = "/home/u/proj/.claude/worktrees/feat-x"

    def _run(self, payload):
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )

    def _worktree_with_allow(self, tmp, read_only):
        wt = os.path.join(tmp, ".claude", "worktrees", "feat-x")
        policy_dir = os.path.join(wt, ".claude", "hooks", "policy")
        os.makedirs(policy_dir)
        toml_list = '["' + '", "'.join(read_only) + '"]'
        with open(os.path.join(policy_dir, "allowlist.toml"), "w") as fh:
            fh.write(
                'schema_version = 1\n'
                '[fail_mode]\nmutating = "deny"\nread_only = "allow"\n'
                f'[allow]\nread_only_commands = {toml_list}\n'
            )
        return wt

    def test_editing_worktree_allowlist_prompts_ask(self):
        path = f"{self.WT}/.claude/hooks/policy/allowlist.toml"
        result = self._run({"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": self.WT})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"permissionDecision": "ask"', result.stdout)
        self.assertIn("allowlist", result.stdout)

    def test_editing_a_normal_worktree_file_is_not_gated(self):
        path = f"{self.WT}/crates/foo/src/lib.rs"
        result = self._run({"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": self.WT})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("permissionDecision", result.stdout)

    def test_bash_honors_worktree_allowlist_directly_no_per_command_prompt(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wt = self._worktree_with_allow(tmp, ["curl", "ls"])  # grants curl (main denies it)
        result = self._run({"tool_name": "Bash", "tool_input": {"command": "curl example.test"}, "cwd": wt})
        self.assertEqual(result.returncode, 0, result.stderr)  # honored, not re-prompted
        self.assertNotIn("permissionDecision", result.stdout)

    def test_bash_denied_when_neither_allowlist_grants(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wt = self._worktree_with_allow(tmp, ["ls"])  # curl in neither
        result = self._run({"tool_name": "Bash", "tool_input": {"command": "curl example.test"}, "cwd": wt})
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
