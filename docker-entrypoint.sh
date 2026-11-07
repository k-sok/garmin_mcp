#!/bin/sh
# Container entrypoint: optionally seed the Garmin token store from the
# GARMIN_TOKENS_JSON environment variable, then start the server.
# See src/garmin_mcp/seed_tokens.py and the GARMIN_TOKENS_JSON entry in the README. Without the variable this is a plain
# `exec garmin-mcp "$@"`, so stdio and docker-compose usage are unchanged.
set -e

if [ -n "${GARMIN_TOKENS_JSON:-}" ]; then
    python -m garmin_mcp.seed_tokens
fi

exec garmin-mcp "$@"
