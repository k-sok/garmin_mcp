# Use Python 3.12 slim image for smaller size
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install uv for faster dependency management
# https://github.com/astral-sh/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# Copy dependency files and README first for better layer caching
COPY pyproject.toml README.md ./

# Copy the application source code (needed for editable install)
COPY src/ ./src/

# Install dependencies using uv
RUN uv pip install -e .

# Copy test files (optional, for testing in container)
COPY tests/ ./tests/
COPY pytest.ini ./

# Create directory for Garmin tokens
RUN mkdir -p /root/.garminconnect && \
    chmod 700 /root/.garminconnect

# Expose the HTTP port. The image defaults to stdio (Claude Desktop, Inspector);
# set GARMIN_MCP_TRANSPORT=streamable-http to serve over this port (e.g. in k8s).
# EXPOSE 8000

# The entrypoint seeds the token store from GARMIN_TOKENS_JSON when it is set
# (see src/garmin_mcp/seed_tokens.py), then starts the MCP server.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

# Health check: probes /healthz when an HTTP transport is configured and passes
# under stdio. Standard library only, because the slim image has no curl/wget
# (see docker-healthcheck.py). /readyz is not used: it stays 503 until a Garmin
# login succeeded.
COPY docker-healthcheck.py /usr/local/bin/docker-healthcheck.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "/usr/local/bin/docker-healthcheck.py"]
