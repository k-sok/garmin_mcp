# Fork rules (k-sok/garmin_mcp)

These rules are specific to this fork. The root `CLAUDE.md` is upstream's and is not changed here.

- First step in a fresh clone: `git config core.hooksPath .githooks`. The `commit-msg` hook there rejects AI attribution.
- No `Co-Authored-By:` line for an AI, no other AI attribution ("Generated with Claude", session links) and no emojis in commit messages or pull request descriptions. Work here goes upstream to Taxuspt/garmin_mcp, whose maintainer does not accept them. Commit bodies still say why. Human co-authors are fine.
- Proposals for upstream are built on branches cut from upstream `main`, so fork-only files (`.claude/CLAUDE.md`, `.claude/settings.json`, `.githooks/`) never go with them.
- Hosted use by fatgu.ai runs with `GARMIN_FILE_TOOLS=off`. Release tags `fatguai-<n>` mark reviewed commits and are never moved. Push to `main` only after `uv run pytest tests/unit/ tests/integration/` passed on that exact commit, because a push to `main` deploys the owner's server (N2).
- Before pushing `main`, run the exact audit CI runs, locally: `uv export --frozen --no-dev --no-emit-project > /tmp/req.txt && uvx pip-audit==2.10.1 -r /tmp/req.txt --require-hashes --disable-pip --strict`. A push deploys, and CI would only tell you afterwards.
- Scheduled workflows are off in a fork by default. Enable Actions and the weekly `security.yml` schedule in the fork's Actions settings, or the weekly dependency audit never runs.
