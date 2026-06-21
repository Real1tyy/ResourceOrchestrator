"""`wt_cli.console.confirm` — y/n prompt with EOF + non-TTY safety."""

from __future__ import annotations

from wt_cli import console


def _force_tty(monkeypatch, *, value: bool) -> None:
    """Pretend stdin is (or isn't) a TTY without monkeying with the real one."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: value)


def test_confirm_returns_false_when_stdin_is_not_a_tty(monkeypatch):
    """CI scripts and lefthook hooks have no TTY. We must default to refusing
    the action rather than blocking forever on `input()`."""
    _force_tty(monkeypatch, value=False)
    assert console.confirm("Continue?") is False


def test_confirm_returns_true_for_yes(monkeypatch):
    _force_tty(monkeypatch, value=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert console.confirm("Continue?") is True


def test_confirm_returns_true_for_yes_word(monkeypatch):
    _force_tty(monkeypatch, value=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert console.confirm("Continue?") is True


def test_confirm_treats_blank_input_as_no(monkeypatch):
    """Default-no UX — bare Enter on `[y/N]` must NOT proceed."""
    _force_tty(monkeypatch, value=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert console.confirm("Continue?") is False


def test_confirm_treats_non_yes_as_no(monkeypatch):
    _force_tty(monkeypatch, value=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert console.confirm("Continue?") is False


def test_confirm_returns_false_on_eof(monkeypatch):
    """Ctrl-D mid-prompt must NOT proceed."""
    _force_tty(monkeypatch, value=True)

    def raise_eof(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)
    assert console.confirm("Continue?") is False


def test_confirm_returns_false_on_keyboard_interrupt(monkeypatch):
    _force_tty(monkeypatch, value=True)

    def raise_kbi(prompt=""):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", raise_kbi)
    assert console.confirm("Continue?") is False


def test_confirm_is_case_insensitive(monkeypatch):
    _force_tty(monkeypatch, value=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "Y")
    assert console.confirm("Continue?") is True


def test_confirm_strips_whitespace(monkeypatch):
    _force_tty(monkeypatch, value=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "  yes  ")
    assert console.confirm("Continue?") is True
