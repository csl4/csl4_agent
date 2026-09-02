"""终端探测与命令映射单测（T020，FR-002 / SC-004）。

覆盖三终端（bash / zsh / powershell）：
- 环境探测（detect_shell）：Windows 默认 PowerShell、Git Bash、zsh；
- 命令写法改写（adapt_command）：bash 同义命令 → PowerShell 写法；
- 跨终端执行（execute_in_shell）：真实 bash 子进程 + 失败时以改写形式重试。

bash/zsh 差异极小（research.md §5），映射表聚焦 bash→powershell。
"""

import sys

import pytest

from agent.core.env.terminal import (
    ShellResult,
    TerminalType,
    adapt_command,
    detect_shell,
    execute_in_shell,
)


class TestDetectShell:
    def test_windows_default_powershell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("SHELL", raising=False)
        assert detect_shell() == TerminalType.POWERSHELL

    def test_windows_git_bash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SHELL", "C:/Program Files/Git/bin/bash.exe")
        assert detect_shell() == TerminalType.BASH

    def test_posix_zsh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("SHELL", "/usr/bin/zsh")
        assert detect_shell() == TerminalType.ZSH

    def test_posix_default_bash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("SHELL", raising=False)
        assert detect_shell() == TerminalType.BASH


class TestAdaptCommand:
    def test_powershell_rewrites_synonym(self) -> None:
        assert adapt_command("ls -la", TerminalType.POWERSHELL) == "Get-ChildItem -la"
        assert adapt_command("cat file.txt", TerminalType.POWERSHELL) == "Get-Content file.txt"
        assert adapt_command("pwd", TerminalType.POWERSHELL) == "Get-Location"

    def test_powershell_rewrites_piped_segments(self) -> None:
        assert (
            adapt_command("ls | grep foo", TerminalType.POWERSHELL)
            == "Get-ChildItem | Select-String foo"
        )

    def test_powershell_rewrites_combined_segments(self) -> None:
        assert (
            adapt_command("pwd && ls", TerminalType.POWERSHELL)
            == "Get-Location && Get-ChildItem"
        )

    def test_powershell_unknown_verb_unchanged(self) -> None:
        assert adapt_command("git status", TerminalType.POWERSHELL) == "git status"

    def test_powershell_empty(self) -> None:
        assert adapt_command("", TerminalType.POWERSHELL) == ""

    def test_zsh_passthrough(self) -> None:
        assert adapt_command("ls -la", TerminalType.ZSH) == "ls -la"

    def test_bash_passthrough(self) -> None:
        assert adapt_command("ls -la", TerminalType.BASH) == "ls -la"


class TestExecuteInShell:
    def test_bash_echo(self) -> None:
        result = execute_in_shell("echo hello-from-terminal", TerminalType.BASH, timeout=10)
        assert result.return_code == 0
        assert "hello-from-terminal" in result.stdout

    def test_bash_timeout(self) -> None:
        result = execute_in_shell("sleep 5", TerminalType.BASH, timeout=1)
        assert result.timed_out is True

    def test_falls_back_to_adapted_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """首次执行失败 → 以目标终端改写形式重试一次（FR-002）。"""
        from agent.core.env import terminal

        calls: list = []
        real = terminal._run_once

        def fake_run_once(cmd, shell, timeout, cwd, bash_path):
            calls.append(cmd)
            if len(calls) == 1:
                return ShellResult(stdout="", return_code=1, timed_out=False)
            return ShellResult(stdout="adapted-ran", return_code=0, timed_out=False)

        monkeypatch.setattr(terminal, "_run_once", fake_run_once)
        try:
            result = execute_in_shell("ls -la", TerminalType.POWERSHELL, timeout=5)
        finally:
            terminal._run_once = real
        assert result.return_code == 0
        assert "adapted-ran" in result.stdout
        assert calls == ["ls -la", "Get-ChildItem -la"]

    def test_no_fallback_when_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """超时不算命令失败，不触发改写重试。"""
        from agent.core.env import terminal

        calls: list = []
        real = terminal._run_once

        def fake_run_once(cmd, shell, timeout, cwd, bash_path):
            calls.append(cmd)
            return ShellResult(stdout="", return_code=None, timed_out=True)

        monkeypatch.setattr(terminal, "_run_once", fake_run_once)
        try:
            result = execute_in_shell("sleep 5", TerminalType.POWERSHELL, timeout=1)
        finally:
            terminal._run_once = real
        assert result.timed_out is True
        assert calls == ["sleep 5"]
