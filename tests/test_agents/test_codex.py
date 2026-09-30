from __future__ import annotations

import subprocess

from secfoo.agents import ADAPTERS, get_adapter
from secfoo.agents.codex import CodexAdapter


def test_registered_under_codex_id():
    assert ADAPTERS["codex"] is CodexAdapter
    assert isinstance(get_adapter("codex"), CodexAdapter)


def test_build_command_is_non_interactive_exec(tmp_path):
    cmd = CodexAdapter().build_command("hello", workdir=tmp_path)
    assert cmd[:2] == ["codex", "exec"]


def test_build_command_is_read_only(tmp_path):
    cmd = CodexAdapter().build_command("hello", workdir=tmp_path)
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "--full-auto" not in cmd


def test_build_command_headless_flags(tmp_path):
    cmd = CodexAdapter().build_command("hello", workdir=tmp_path)
    assert "--skip-git-repo-check" in cmd
    assert "--ephemeral" in cmd
    assert cmd[cmd.index("--color") + 1] == "never"
    assert "--json" not in cmd  # report must stay plain text on stdout


def test_build_command_sets_working_root(tmp_path):
    cmd = CodexAdapter().build_command("hello", workdir=tmp_path)
    assert cmd[cmd.index("--cd") + 1] == str(tmp_path)


def test_prompt_is_read_from_stdin_not_argv(fake_popen, tmp_path):
    """On Windows `codex` is a .cmd shim; cmd.exe truncates an argv prompt
    at its first newline, so `codex exec -` reads it from stdin instead."""
    fake = fake_popen(stdout="ok", returncode=0)
    prompt = "--looks-like-a-flag\nline two"
    CodexAdapter().run(prompt, workdir=tmp_path)
    argv = fake.call_args[0]
    assert argv[-1] == "-"
    assert prompt not in argv
    assert fake.call_kwargs["stdin"] == subprocess.PIPE
    assert fake.stdin_input == prompt


def test_sandbox_rejected_commands_is_failed_not_success(fake_popen, tmp_path):
    """Real stderr from a Windows E2E run (SECFOO-10): every read was
    refused, yet `codex exec` exited 0 with a "could not inspect" report."""
    stderr = (
        '2026-09-30T02:21:59Z ERROR codex_core::tools::router: error=exec_command failed: CreateProcess '
        '{ message: "Rejected(\\"`powershell.exe -Command \'Get-ChildItem\'` rejected: blocked by policy\\")" }'
    )
    fake_popen(returncode=0, stdout="# SAST Report\n\nI could not inspect the target.", stderr=stderr)
    result = CodexAdapter().run("review", workdir=tmp_path)
    assert result.status == "failed"
    assert result.raw_report == ""
    assert "never read" in result.stderr


def test_clean_run_stays_success(fake_popen, tmp_path):
    fake_popen(returncode=0, stdout="# SAST Report\n\nfindings", stderr="exec\nrg --files\n")
    assert CodexAdapter().run("review", workdir=tmp_path).status == "success"


def test_extract_report_is_stdout_passthrough():
    assert CodexAdapter().extract_report("final message") == "final message"
