from __future__ import annotations

import json
import subprocess

from secfoo.agents.copilot import CopilotAdapter


def test_build_command(tmp_path):
    adapter = CopilotAdapter()
    cmd = adapter.build_command("hi", workdir=tmp_path)
    assert cmd == ["copilot", "--output-format", "json", "--allow-all-tools"]


def test_prompt_goes_to_stdin_not_argv(fake_popen, tmp_path):
    """On Windows `copilot` is a .bat shim; cmd.exe truncates a `-p` argv
    prompt at its first newline, so the prompt must be piped instead."""
    fake = fake_popen(stdout="ok", returncode=0)
    prompt = "line one\nline two"
    CopilotAdapter().run(prompt, workdir=tmp_path)
    assert fake.call_kwargs["stdin"] == subprocess.PIPE
    assert fake.stdin_input == prompt
    assert prompt not in fake.call_args[0]


def test_build_command_never_grants_unrestricted_paths_or_urls(tmp_path):
    """--allow-all-tools is required for non-interactive mode, but the
    broader --allow-all/--yolo/--allow-all-paths/--allow-all-urls escape
    hatches are a materially bigger grant (arbitrary filesystem/network
    access) that a read-only security review never needs.
    """
    adapter = CopilotAdapter()
    cmd = adapter.build_command("hi", workdir=tmp_path)
    for forbidden in ("--allow-all", "--yolo", "--allow-all-paths", "--allow-all-urls"):
        assert forbidden not in cmd


def test_binary_and_name():
    assert CopilotAdapter.binary == "copilot"
    assert CopilotAdapter.name == "copilot"


def test_extract_report_falls_back_to_raw_stdout_when_not_jsonl():
    adapter = CopilotAdapter()
    assert adapter.extract_report("# SAST Report\n...") == "# SAST Report\n..."


def test_extract_report_takes_main_agent_answer_not_builtin_subagent():
    """Event shapes captured from a real CLI 1.0.89 run (SECFOO-10): the
    built-in "security-review" subagent's messages carry an agentId and
    must not leak into (or interleave with) the report."""
    events = [
        {"type": "session.tools_updated", "data": {}},
        {"type": "assistant.message", "data": {"content": ""}},
        {"type": "subagent.started", "data": {"agentName": "security-review"}, "agentId": "sub-1"},
        {"type": "assistant.message", "data": {"content": "## Security Findings\n### Alert 1", "phase": "final_answer"},
         "agentId": "sub-1"},
        {"type": "assistant.message_delta", "data": {"deltaContent": "# Secret"}},
        {"type": "assistant.message", "data": {"content": "# Secret Scanning Report\n\n## 1. Executive Summary",
                                               "phase": "final_answer"}},
        {"type": "result", "exitCode": 0},
    ]
    stdout = "\n".join(json.dumps(e) for e in events)
    assert CopilotAdapter().extract_report(stdout) == "# Secret Scanning Report\n\n## 1. Executive Summary"


def test_is_available_uses_binary_name(monkeypatch):
    monkeypatch.setattr("secfoo.agents.base.shutil.which", lambda name: "/usr/bin/copilot" if name == "copilot" else None)
    assert CopilotAdapter().is_available() is True


def test_is_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr("secfoo.agents.base.shutil.which", lambda name: None)
    assert CopilotAdapter().is_available() is False


def test_run_success(fake_popen, tmp_path):
    fake_popen(stdout="# SAST Report\n\nfindings...", stderr="", returncode=0)
    adapter = CopilotAdapter()
    result = adapter.run("hi", workdir=tmp_path)
    assert result.status == "success"
    assert result.raw_report == "# SAST Report\n\nfindings..."


def test_run_timeout_does_not_raise(fake_popen, tmp_path):
    fake_popen(raise_timeout=True, stdout="partial", stderr="")
    adapter = CopilotAdapter()
    result = adapter.run("hi", workdir=tmp_path, timeout=1)
    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.raw_report == ""
