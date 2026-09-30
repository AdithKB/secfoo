"""OpenAI Codex CLI adapter.

Flags verified against a real `codex exec --help` (codex-cli 0.155, Sep 2026):
`codex exec` runs a single prompt non-interactively. Progress/tool activity
goes to stderr and only the agent's final message is printed to stdout, so
the base class's default `extract_report` (stdout is the report) is correct
here and deliberately not overridden. `--json` would switch stdout to a JSONL
event stream, which we do not want for a plain-markdown report.

Deliberately NOT passed:
- `--dangerously-bypass-approvals-and-sandbox` / `--full-auto`: a read-only
  review never needs write access, so we pin `--sandbox read-only` instead.
- `--json`: see above.

MCP servers from ~/.secfoo/config.toml are not wired up for Codex yet
(`secfoo mcp sync --agent codex` is a follow-up); Codex runs use whatever is
already in the user's own ~/.codex/config.toml.
"""

from __future__ import annotations

import os
from pathlib import Path

from secfoo.agents.base import AgentAdapter


class CodexAdapter(AgentAdapter):
    name = "codex"
    binary = "codex"
    default_timeout_seconds = 1800
    # `codex exec -` reads the prompt from stdin; passing it as argv breaks
    # on Windows, where `codex` is a `.cmd` shim and cmd.exe truncates the
    # argument at its first newline.
    prompt_via_stdin = True

    def detect_failure(self, stdout: str, stderr: str) -> str | None:
        # On Windows without Codex's (admin-installed) sandbox, `--sandbox
        # read-only` refuses *every* shell command, reads included, and
        # `codex exec` still exits 0 with a "could not inspect" report
        # (SECFOO-10 E2E, codex-cli 0.159.2). Never record that as success.
        if "exec_command failed" not in stderr:
            return None
        hint = ""
        if os.name == "nt":
            hint = (" On Windows, Codex's read-only sandbox needs a one-time admin setup"
                    " (set windows.sandbox = \"elevated\" in ~/.codex/config.toml and approve the prompt).")
        return "Codex's sandbox rejected its commands, so the target was never read." + hint

    def build_command(self, prompt: str, *, workdir: Path) -> list[str]:
        return [
            self.binary,
            "exec",
            # Read-only filesystem: the assessment only reads the target.
            "--sandbox",
            "read-only",
            # secfoo runs against arbitrary targets (cloned repos, user
            # paths, non-git dirs); without this Codex refuses to start
            # outside a trusted git repository.
            "--skip-git-repo-check",
            # Don't litter ~/.codex/sessions with one file per assessment.
            "--ephemeral",
            # No ANSI escapes in captured stdout/stderr.
            "--color",
            "never",
            "--cd",
            str(workdir),
            # `-`: read the prompt from stdin (see prompt_via_stdin).
            "-",
        ]
