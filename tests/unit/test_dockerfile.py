"""The image does not run as root and installs from the lock (#8, M3, L49)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (REPO / "docker-compose.yml").read_text(encoding="utf-8")


def _instructions(text: str) -> list[str]:
    """Dockerfile instructions with continuation lines joined, comments dropped."""
    lines, current = [], ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not current and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        lines.append((current + stripped).strip())
        current = ""
    return lines


INSTRUCTIONS = _instructions(DOCKERFILE)


def _of(keyword: str) -> list[tuple[int, str]]:
    return [
        (index, line)
        for index, line in enumerate(INSTRUCTIONS)
        if line.split(None, 1)[0].upper() == keyword
    ]


def test_the_image_ends_as_an_unprivileged_numeric_user():
    users = _of("USER")
    last_user_index, last_user = users[-1]
    match = re.fullmatch(r"USER (\d+):(\d+)", last_user)

    assert match, last_user
    assert int(match.group(1)) != 0 and int(match.group(2)) != 0
    assert last_user_index > _of("RUN")[-1][0]
    token_dir = [line for _, line in _of("RUN") if "/home/garmin/.garminconnect" in line]
    assert token_dir, "no RUN creates the token directory in the user's home"
    assert "chown 10001:10001 /home/garmin/.garminconnect" in token_dir[0]
    assert "chmod 700 /home/garmin/.garminconnect" in token_dir[0]
    assert f"--uid {match.group(1)}" in token_dir[0]


def test_the_image_installs_exactly_the_locked_dependencies():
    runs = [line for _, line in _of("RUN")]
    copies = [line for _, line in _of("COPY")]

    assert any("uv sync --frozen --no-dev" in run for run in runs)
    assert not any("uv pip install" in run for run in runs)
    assert any("uv.lock" in copy for copy in copies)
    assert not any(re.search(r"\btests/?(\s|$)", copy) for copy in copies)
    assert not any("pytest.ini" in copy for copy in copies)


def test_the_runtime_user_owns_no_build_cache_and_no_installed_code():
    runs = [line for _, line in _of("RUN")]
    sync_index, sync = next(
        (index, line) for index, line in _of("RUN") if "uv sync" in line
    )
    home_index = next(
        index for index, line in _of("ENV") if re.search(r"\bHOME=", line)
    )

    assert "--no-cache" in sync
    assert sync_index < home_index, "uv sync must run before ENV HOME is set"
    assert re.search(r"\bUV_LINK_MODE=copy\b", " ".join(l for _, l in _of("ENV")))
    assert "chmod -R go-w /app" in sync, "/app/.venv/.lock must not be group/world writable"
    chowns = [part for run in runs for part in re.findall(r"chown [^&]*", run)]
    assert chowns == ["chown 10001:10001 /home/garmin/.garminconnect "], chowns
    assert not any("--create-home" in run or " -m " in run for run in runs)


def test_the_base_image_and_the_uv_image_are_pinned_by_digest():
    from_lines = [line for _, line in _of("FROM")]
    copy_from = [line for _, line in _of("COPY") if "--from=" in line]

    assert len(from_lines) == 1
    assert re.search(r"python:3\.12-slim@sha256:[0-9a-f]{64}\b", from_lines[0])
    for line in copy_from:
        assert re.search(r"--from=\S+:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}\s", line), line


def test_the_uv_image_is_pinned():
    sources = [
        re.search(r"--from=(\S+)", line).group(1)
        for _, line in _of("COPY")
        if "--from=" in line
    ]

    assert sources
    for source in sources:
        assert re.search(r":\d+\.\d+\.\d+(@|$)", source), source
        assert not source.endswith(":latest")


def test_the_image_has_file_tools_off():
    env = " ".join(line for _, line in _of("ENV"))

    assert "GARMIN_FILE_TOOLS=off" in env
    assert "HOME=/home/garmin" in env
    assert '/app/.venv/bin:$PATH' in env


def test_the_compose_file_follows_the_new_token_location():
    volumes = re.findall(r"^\s*-\s*(\S+:\S+)\s*$", COMPOSE, re.M)
    token_volumes = [v for v in volumes if ".garminconnect" in v]

    assert token_volumes == ["garmin-tokens:/home/garmin/.garminconnect"]
    live = "\n".join(
        line for line in COMPOSE.splitlines() if not line.lstrip().startswith("#")
    )
    assert "/root" not in live
    assert re.search(r"(?m)^    security_opt:\n      - no-new-privileges:true$", live)
    assert re.search(r"(?m)^    cap_drop:\n      - ALL$", live)
    assert re.search(r"(?m)^    read_only: true$", live)
    assert re.search(r"(?m)^    tmpfs:\n      - /tmp$", live)
