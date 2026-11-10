"""Fork rules: no AI attribution in commits (see .claude/CLAUDE.md)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".githooks" / "commit-msg"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell hook")


def _run_hook(tmp_path: Path, message: str) -> subprocess.CompletedProcess:
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["sh", str(HOOK), str(msg_file)], capture_output=True, text=True
    )


def test_the_hook_rejects_a_claude_co_author_line(tmp_path):
    result = _run_hook(
        tmp_path,
        "feat(x): add y\n\nbody\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n",
    )

    assert result.returncode == 1
    assert "Co-Authored-By" in result.stderr


def test_the_hook_rejects_a_noreply_anthropic_co_author_whatever_the_name(tmp_path):
    result = _run_hook(
        tmp_path, "fix: z\n\nco-authored-by: Bot <noreply@anthropic.com>\n"
    )

    assert result.returncode == 1


@pytest.mark.parametrize(
    "line",
    [
        "Co-authored-by: Copilot <copilot@github.com>",
        "Co-Authored-By: GitHub Copilot <175728472+Copilot@users.noreply.github.com>",
        "Co-authored-by: OpenAI Codex <codex@openai.com>",
        "co-authored-by: ChatGPT <noreply@example.org>",
        "Co-authored-by: Gemini <gemini@google.com>",
        "Co-authored-by: Cursor Agent <cursoragent@cursor.com>",
        "Co-authored-by: Some Bot <bot@openai.com>",
    ],
)
def test_the_hook_rejects_co_author_lines_for_other_ai_tools(tmp_path, line):
    result = _run_hook(tmp_path, f"fix(x): handle y\n\n{line}\n")

    assert result.returncode == 1, line


def test_the_hook_rejects_a_generated_with_claude_line(tmp_path):
    result = _run_hook(
        tmp_path, "fix: z\n\nbody\n\nGenerated with [Claude Code](https://claude.com)\n"
    )

    assert result.returncode == 1


def test_the_hook_rejects_a_claude_ai_code_link(tmp_path):
    result = _run_hook(
        tmp_path, "fix: z\n\nSession: https://claude.ai/code/session_abc\n"
    )

    assert result.returncode == 1


def test_the_hook_accepts_a_plain_message(tmp_path):
    result = _run_hook(tmp_path, "fix(x): handle y\n\nWhy: because z.\n")

    assert result.returncode == 0


def test_the_hook_accepts_a_human_co_author(tmp_path):
    result = _run_hook(
        tmp_path, "fix(x): handle y\n\nCo-Authored-By: Ada Lovelace <ada@example.org>\n"
    )

    assert result.returncode == 0


def test_the_hook_ignores_comment_lines_of_the_commit_template(tmp_path):
    result = _run_hook(
        tmp_path, "fix(x): handle y\n\n# Co-Authored-By: Claude is not allowed\n"
    )

    assert result.returncode == 0


def test_the_hook_is_executable():
    assert os.access(HOOK, os.X_OK)


def test_the_settings_turn_ai_attribution_off():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())

    assert settings["attribution"] == {"commit": "", "pr": ""}
    assert settings["includeCoAuthoredBy"] is False


def test_gitattributes_keeps_the_hook_on_unix_line_endings():
    attributes = (REPO / ".gitattributes").read_text()

    assert ".githooks/* text eol=lf" in attributes
