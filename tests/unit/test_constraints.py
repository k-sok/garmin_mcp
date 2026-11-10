"""``constraints.txt`` pins what ``uv.lock`` locks, for ``uvx -c`` on a member's computer (L49)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PIN = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)", re.MULTILINE)


def _pins(text: str) -> dict[str, str]:
    return {name.lower(): version for name, version in PIN.findall(text)}


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is needed to export the lock")
def test_the_constraints_file_matches_the_lock():
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    constraints = (REPO / "constraints.txt").read_text()

    assert _pins(constraints) == _pins(exported)
    assert "garminconnect" in _pins(constraints)


def test_the_constraints_file_pins_every_package_exactly_without_hashes():
    lines = [
        line
        for line in (REPO / "constraints.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lines
    assert all(PIN.match(line) for line in lines)
    assert not any("--hash" in line for line in lines)
