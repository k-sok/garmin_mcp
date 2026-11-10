"""CI audits the locked dependencies with pip-audit (#8, L9).

The workflows are read as text (no YAML dependency): each step starts at a
line `    - name:` and runs to the next one.
"""

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _steps(text: str) -> list[str]:
    return re.split(r"(?m)^(?=    - name:)", text)[1:]


def _audit_steps(text: str) -> list[str]:
    return [step for step in _steps(text) if "pip-audit" in step]


@pytest.mark.parametrize("name", ["ci.yml", "security.yml"])
def test_the_workflows_run_the_audit(name):
    text = _text(name)
    steps = _audit_steps(text)

    assert len(steps) == 1, name
    step = steps[0]
    assert "uv export --frozen --no-dev --no-emit-project" in step
    assert re.search(r"pip-audit==\d+\.\d+\.\d+", step)
    assert "--require-hashes" in step
    assert "--disable-pip" in step
    assert "continue-on-error" not in text
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if "--ignore-vuln" in line:
            comment = lines[number - 1]
            assert re.search(r"#.*\b[A-Z]\d+\b", comment), (
                "an ignored advisory needs a comment with a reason and a risk ID"
            )


def test_the_audit_runs_on_push_pull_request_and_weekly():
    ci_header = _text("ci.yml").split("jobs:")[0]
    security_header = _text("security.yml").split("jobs:")[0]

    assert re.search(r"(?m)^  push:", ci_header)
    assert re.search(r"(?m)^  pull_request:", ci_header)
    assert re.search(r"(?m)^  schedule:", security_header)
    assert "cron:" in security_header


def test_the_placeholder_comment_is_gone():
    assert "Add pip-audit or safety scan here" not in _text("security.yml")


WORKFLOW_FILES = sorted(p.name for p in WORKFLOWS.glob("*.yml"))


@pytest.mark.parametrize("name", WORKFLOW_FILES)
def test_actions_are_pinned_by_commit_with_a_version_comment(name):
    uses = re.findall(r"(?m)^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", _text(name))

    assert uses
    for action, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
        assert re.search(r"#\s*v\d+(\.\d+)*\s*$", rest), f"{action} needs a version comment"


@pytest.mark.parametrize("name", WORKFLOW_FILES)
def test_the_workflows_ask_for_a_read_only_token(name):
    text = _text(name)
    header = text.split("\njobs:\n")[0]

    assert re.search(r"(?m)^permissions:\n  contents: read$", header)
    assert not re.search(r"(?m)^\s+[\w-]+: write$", text)


@pytest.mark.parametrize("name", ["ci.yml", "security.yml"])
def test_the_audit_job_has_its_own_read_only_permissions(name):
    text = _text(name)
    job = re.split(r"(?m)^  (?=[\w-]+:\n    runs-on)", text)
    audit_jobs = [part for part in job if "pip-audit" in part]

    assert len(audit_jobs) == 1
    assert re.search(r"(?m)^    permissions:\n      contents: read$", audit_jobs[0])
