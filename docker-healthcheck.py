#!/usr/bin/env python3
"""Container HEALTHCHECK for the HTTP transports.

The slim base image has neither curl nor wget, which is what most orchestrators
(Coolify, Docker Swarm templates, ...) use to probe a container, so this uses
only the standard library. It asks the server's own ``/healthz`` endpoint
(liveness: the HTTP server is up). ``/readyz`` is deliberately not used: it
stays 503 until a Garmin login succeeded, and a container that is waiting for
tokens should still count as running.

With the default stdio transport there is no HTTP server to probe, so the check
passes: stdio users must not see their container flagged unhealthy.
"""

import os
import sys
import urllib.request

HTTP_TRANSPORTS = ("streamable-http", "sse")
WILDCARD_HOSTS = ("", "0.0.0.0", "::")


def health_url(environ) -> str | None:
    """URL to probe, or None when the transport serves no HTTP."""
    transport = environ.get("GARMIN_MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in HTTP_TRANSPORTS:
        return None
    host = environ.get("GARMIN_MCP_HOST", "127.0.0.1").strip().strip("[]")
    if host in WILDCARD_HOSTS:
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    port = environ.get("GARMIN_MCP_PORT", "8000").strip() or "8000"
    return f"http://{host}:{port}/healthz"


def main(environ=None, opener=urllib.request.urlopen) -> int:
    url = health_url(os.environ if environ is None else environ)
    if url is None:
        return 0
    try:
        with opener(url, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
