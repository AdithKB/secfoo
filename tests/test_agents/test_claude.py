from __future__ import annotations

import json

from secfoo.agents.claude import ClaudeAdapter
from secfoo.settings import MCPServerConfig


def test_build_command_argv(tmp_path, monkeypatch):
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    adapter = ClaudeAdapter()
    cmd = adapter.build_command("hello", workdir=tmp_path)
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "hello" in cmd
    assert "--allowedTools" in cmd
    assert "--permission-mode" in cmd
    assert "default" in cmd


def test_build_command_no_mcp_config_flag_when_no_servers_configured(tmp_path, monkeypatch):
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    adapter = ClaudeAdapter()
    cmd = adapter.build_command("hello", workdir=tmp_path)
    assert "--mcp-config" not in cmd


def test_build_command_adds_mcp_config_and_server_level_tool_grant(tmp_path, monkeypatch):
    server = MCPServerConfig(name="Atlassian-Rovo-MCP", command="npx", args=["-y", "mcp-remote@latest"])
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _config_with([server]))
    generated_path = tmp_path / "claude-mcp-config.json"
    monkeypatch.setattr("secfoo.agents.claude.write_claude_mcp_config", lambda servers: generated_path)

    adapter = ClaudeAdapter()
    cmd = adapter.build_command("hello", workdir=tmp_path)

    assert "--mcp-config" in cmd
    assert str(generated_path) in cmd
    allowed_tools_idx = cmd.index("--allowedTools") + 1
    assert "mcp__Atlassian-Rovo-MCP" in cmd[allowed_tools_idx]


def test_build_command_ignores_the_targets_own_claude_settings(tmp_path, monkeypatch):
    """SECFOO-40: the target is untrusted and `-p` skips the trust prompt,
    so its `.claude/settings.json` (hooks, permissions.allow) must not be
    loaded. Only the user's own settings may apply."""
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    cmd = ClaudeAdapter().build_command("hello", workdir=tmp_path)
    assert cmd[cmd.index("--setting-sources") + 1] == "user"


def test_build_command_only_loads_mcp_servers_secfoo_passes(tmp_path, monkeypatch):
    """SECFOO-40: a `.mcp.json` in the target must never be started, with or
    without secfoo-configured servers."""
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    assert "--strict-mcp-config" in ClaudeAdapter().build_command("hello", workdir=tmp_path)

    server = MCPServerConfig(name="Atlassian-Rovo-MCP", command="npx", args=["-y", "mcp-remote@latest"])
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _config_with([server]))
    monkeypatch.setattr("secfoo.agents.claude.write_claude_mcp_config", lambda servers: tmp_path / "mcp.json")
    cmd = ClaudeAdapter().build_command("hello", workdir=tmp_path)
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" in cmd


def test_build_command_never_bypasses_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    cmd = ClaudeAdapter().build_command("hello", workdir=tmp_path)
    assert "--dangerously-skip-permissions" not in cmd
    assert "bypassPermissions" not in cmd


def _empty_config():
    from secfoo.settings import Defaults, SecfooConfig

    return SecfooConfig(defaults=Defaults(), mcp_servers=[])


def _config_with(servers):
    from secfoo.settings import Defaults, SecfooConfig

    return SecfooConfig(defaults=Defaults(), mcp_servers=servers)


def test_extract_report_parses_json_result_field():
    adapter = ClaudeAdapter()
    stdout = json.dumps({"result": "# Report\ncontent"})
    assert adapter.extract_report(stdout) == "# Report\ncontent"


def test_extract_report_falls_back_to_raw_stdout_on_non_json():
    adapter = ClaudeAdapter()
    assert adapter.extract_report("not json") == "not json"


def test_run_success_extracts_report_from_json(fake_popen, tmp_path, monkeypatch):
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    fake_popen(returncode=0, stdout=json.dumps({"result": "# Threat Report"}), stderr="")
    adapter = ClaudeAdapter()
    result = adapter.run("review this", workdir=tmp_path)
    assert result.status == "success"
    assert result.raw_report == "# Threat Report"


def test_extract_usage_reads_cost_and_sums_cached_input_tokens():
    stdout = json.dumps({
        "result": "report",
        "total_cost_usd": 0.4512,
        "usage": {
            "input_tokens": 100,
            "cache_creation_input_tokens": 2000,
            "cache_read_input_tokens": 30000,
            "output_tokens": 4000,
        },
    })
    usage = ClaudeAdapter().extract_usage(stdout)
    assert usage.input_tokens == 32100
    assert usage.output_tokens == 4000
    assert usage.cost_usd == 0.4512


def test_extract_usage_unknown_when_stdout_is_not_json():
    usage = ClaudeAdapter().extract_usage("plain text")
    assert usage.cost_usd is None
    assert usage.input_tokens is None


def test_run_records_usage_from_json_output(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    stdout = json.dumps({"result": "report", "total_cost_usd": 1.5, "usage": {"input_tokens": 10, "output_tokens": 5}})
    fake_popen(returncode=0, stdout=stdout)
    result = ClaudeAdapter().run("prompt", workdir=tmp_path)
    assert result.cost_usd == 1.5
    assert result.input_tokens == 10
    assert result.output_tokens == 5


# Captured from a real `claude -p ... --max-budget-usd 0.0001` run (Claude
# Code 2.1.288): exit code 1, empty stderr, and no "result" key.
_ERROR_RESULT = json.dumps({
    "type": "result",
    "subtype": "error_max_budget_usd",
    "is_error": True,
    "num_turns": 1,
    "errors": ["Reached maximum budget ($0.0001)"],
    "total_cost_usd": 0.000965,
    "usage": {"input_tokens": 0, "output_tokens": 0},
})


def test_describe_failure_reads_subtype_and_errors_from_stdout_json():
    reason = ClaudeAdapter().describe_failure(_ERROR_RESULT, "")
    assert reason == "Claude Code reported error_max_budget_usd: Reached maximum budget ($0.0001)"


def test_describe_failure_falls_back_to_result_text():
    stdout = json.dumps({"is_error": True, "subtype": "success", "result": "Invalid API key"})
    assert ClaudeAdapter().describe_failure(stdout, "") == "Claude Code reported success: Invalid API key"


def test_describe_failure_is_none_without_an_error_result():
    adapter = ClaudeAdapter()
    assert adapter.describe_failure(json.dumps({"is_error": False, "result": "report"}), "") is None
    assert adapter.describe_failure("not json", "") is None
    assert adapter.describe_failure("[]", "") is None


def test_run_failed_records_the_reason_claude_put_in_stdout(fake_popen, tmp_path, monkeypatch):
    """SECFOO-41: stderr is empty on a Claude error result, so without this
    the run was saved as failed with no explanation."""
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    fake_popen(returncode=1, stdout=_ERROR_RESULT, stderr="")
    result = ClaudeAdapter().run("review this", workdir=tmp_path)
    assert result.status == "failed"
    assert result.raw_report == ""
    assert result.stderr.startswith("secfoo: Claude Code reported error_max_budget_usd")
    assert "Reached maximum budget" in result.stderr
    # What the failed run did spend is still recorded.
    assert result.cost_usd == 0.000965


def test_run_failed_keeps_stderr_when_stdout_has_no_error_result(fake_popen, tmp_path, monkeypatch):
    monkeypatch.setattr("secfoo.agents.claude.load_config", lambda: _empty_config())
    fake_popen(returncode=1, stdout="", stderr="error: unknown option '--nope'")
    result = ClaudeAdapter().run("review this", workdir=tmp_path)
    assert result.status == "failed"
    assert result.stderr == "error: unknown option '--nope'"


def test_extract_report_falls_back_to_raw_stdout_when_result_is_not_text():
    adapter = ClaudeAdapter()
    assert adapter.extract_report("[]") == "[]"
    assert adapter.extract_report(_ERROR_RESULT) == _ERROR_RESULT
    null_result = json.dumps({"result": None})
    assert adapter.extract_report(null_result) == null_result
