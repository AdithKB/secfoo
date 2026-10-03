from __future__ import annotations

import json
from pathlib import Path

from secfoo.agents.base import AgentAdapter, Usage
from secfoo.mcp import write_claude_mcp_config
from secfoo.settings import load_config

# Minimal read-only tool grant: the assessment only needs to read the
# target and look at git history/diffs, never to edit anything.
ALLOWED_TOOLS = "Read,Grep,Glob,Bash(git log:*),Bash(git diff:*)"


def _result_payload(stdout: str) -> dict | None:
    """The `--output-format json` result object, or None when stdout isn't
    one (plain text from an older CLI, or JSON that isn't an object)."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class ClaudeAdapter(AgentAdapter):
    name = "claude"
    binary = "claude"
    default_timeout_seconds = 1800
    # SECFOO-42: with no prompt argument, `claude -p` reads the prompt from
    # stdin. Passing it as argv breaks an npm install on Windows, where
    # `claude` is a `.cmd` shim and cmd.exe cuts the argument at its first
    # newline (the same failure PR #23 fixed for Codex and Copilot).
    prompt_via_stdin = True

    def build_command(self, prompt: str, *, workdir: Path) -> list[str]:
        # MCP servers from ~/.secfoo/config.toml are injected here, scoped
        # to just this invocation (claude's --mcp-config), never touching
        # the user's own global Claude config.
        mcp_servers = load_config().mcp_servers
        mcp_config_path = write_claude_mcp_config(mcp_servers)

        # `mcp__<server-name>` (server-level, no tool suffix) grants every
        # tool that server exposes -- verified directly: without this, a
        # configured MCP server is loaded but every tool call on it is
        # silently denied (shows up in the report as "permission grant was
        # not available"), which looks identical to the server not being
        # connected at all unless you go dig through stderr.
        allowed_tools = ALLOWED_TOOLS
        if mcp_config_path:
            allowed_tools += "," + ",".join(f"mcp__{server.name}" for server in mcp_servers)

        cmd = [
            self.binary,
            # No prompt here: it arrives on stdin (see prompt_via_stdin).
            "-p",
            "--output-format",
            "json",
            "--allowedTools",
            allowed_tools,
            "--permission-mode",
            "default",
            # SECFOO-40: the target is untrusted, and `-p` skips Claude
            # Code's workspace-trust prompt. Without these two flags the
            # target's own `.claude/settings.json` is loaded: its hooks run
            # as shell commands, and `enableAllProjectMcpServers` starts
            # whatever its `.mcp.json` names -- both reproduced on Claude
            # Code 2.1.288. `--setting-sources user` drops the project and
            # local settings (the user's own still apply);
            # `--strict-mcp-config` drops every MCP server except the ones
            # secfoo passes via `--mcp-config` below, which also covers a
            # user whose own settings auto-approve project servers.
            "--setting-sources",
            "user",
            "--strict-mcp-config",
        ]
        if mcp_config_path:
            cmd += ["--mcp-config", str(mcp_config_path)]
        return cmd

    def extract_report(self, stdout: str) -> str:
        payload = _result_payload(stdout)
        result = payload.get("result") if payload is not None else None
        return result if isinstance(result, str) else stdout

    def describe_failure(self, stdout: str, stderr: str) -> str | None:
        # SECFOO-41: on an error result Claude Code exits 1 with an empty
        # stderr and puts the reason in the stdout JSON, e.g.
        #   {"is_error": true, "subtype": "error_max_budget_usd",
        #    "errors": ["Reached maximum budget ($0.0001)"]}
        # (no "result" key; captured from Claude Code 2.1.288). Some error
        # results carry their message in "result" instead, so fall back to
        # that.
        payload = _result_payload(stdout)
        if payload is None or not payload.get("is_error"):
            return None
        errors = payload.get("errors")
        details = [str(error) for error in errors] if isinstance(errors, list) else []
        result = payload.get("result")
        if not details and isinstance(result, str) and result.strip():
            details = [result.strip()]
        reason = f"Claude Code reported {payload.get('subtype') or 'an error'}"
        return f"{reason}: {'; '.join(details)}" if details else reason

    def extract_usage(self, stdout: str) -> Usage:
        # `--output-format json` reports total_cost_usd plus a usage block.
        # On a subscription login the cost is claude's API-price estimate,
        # not an amount actually billed.
        payload = _result_payload(stdout)
        if payload is None:
            return Usage()
        usage = payload.get("usage") or {}
        input_tokens = sum(
            usage.get(key) or 0
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        cost = payload.get("total_cost_usd")
        return Usage(
            input_tokens=input_tokens if usage else None,
            output_tokens=usage.get("output_tokens"),
            cost_usd=float(cost) if cost is not None else None,
        )
