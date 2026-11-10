# Python 3.12 slim, pinned by digest (the tag is kept for readers; the digest is
# what is pulled). Update both together after reviewing the new image.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Set working directory
WORKDIR /app

# Install uv for faster dependency management. Pinned to an exact version and
# digest so a rebuilt image never picks up a different resolver than the one
# that was reviewed (uv 0.11.32). https://github.com/astral-sh/uv
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv

# The environment lives in /app/.venv and is put on PATH, so the entrypoint,
# the health check and `garmin-mcp-auth` run from the locked dependencies.
# GARMIN_FILE_TOOLS=off removes the tools that read or write local files, since
# the container's user must not be steered into touching other files. Set it to
# "on" at run time only for a single-user container that wants them.
# UV_LINK_MODE=copy and UV_COMPILE_BYTECODE=1: installed files are real copies
# with bytecode already compiled, so the read-only root of the compose file
# needs no cache or write access at run time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    GARMIN_FILE_TOOLS=off

# Copy dependency files and README first for better layer caching
COPY pyproject.toml uv.lock README.md ./

# Copy the application source code (needed for the project install)
COPY src/ ./src/

# Install exactly what uv.lock says, without dev dependencies and without a
# cache, so the image holds no build cache. No tests are copied into the image.
# Everything under /app stays owned by root and is not writable by others (uv
# leaves .venv/.lock group/world writable under some umasks).
RUN uv sync --frozen --no-dev --no-cache && \
    chmod -R go-w /app

# The entrypoint seeds the token store from GARMIN_TOKENS_JSON when it is set
# (see src/garmin_mcp/seed_tokens.py), then starts the MCP server. The health
# check probes /healthz when an HTTP transport is configured and passes under
# stdio. Standard library only, because the slim image has no curl/wget (see
# docker-healthcheck.py). /readyz is not used: it stays 503 until a Garmin
# login succeeded.
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chmod=755 docker-healthcheck.py /usr/local/bin/docker-healthcheck.py

# Run as a numeric unprivileged user. Its home and the code stay owned by root;
# only the token directory belongs to the user (mode 700). Mount the token
# volume on /home/garmin/.garminconnect; an existing volume owned by root needs
# `chown -R 10001:10001` once (see the README).
RUN groupadd --gid 10001 garmin && \
    useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /home/garmin --shell /usr/sbin/nologin garmin && \
    mkdir -p /home/garmin/.garminconnect && \
    chown 10001:10001 /home/garmin/.garminconnect && \
    chmod 700 /home/garmin/.garminconnect

# HOME is set after the install, so nothing above wrote under the runtime
# user's home.
ENV HOME=/home/garmin

# Expose the HTTP port. The image defaults to stdio (Claude Desktop, Inspector);
# set GARMIN_MCP_TRANSPORT=streamable-http to serve over this port (e.g. in k8s).
# EXPOSE 8000

USER 10001:10001

ENTRYPOINT ["docker-entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "/usr/local/bin/docker-healthcheck.py"]
