# jira-refine

A portable skill for refining a Jira ticket into a parallelizable epic /
story / task / spike breakdown. See `SKILL.md` for the full instructions —
it's the file any agent tool actually reads.

## Install

From the repo root, use the generic installer (see [`../install.sh`](../install.sh)):

```
../install.sh jira-refine                 # symlink into ~/.claude/skills and ~/.config/opencode/skills
../install.sh jira-refine --agent claude  # just Claude Code
../install.sh jira-refine --target /path/to/some/other/tools/skills/dir
```

## Required: jira-mcp MCP server

This skill uses the `jira-mcp` MCP server, which lives in the shared
[`../mcp/`](../mcp/) directory alongside the server script.

Register it with Claude Code:

```bash
# Register globally (user scope) — simplest
../mcp/setup-jira-mcp.sh

# Pass a credentials .env file
../mcp/setup-jira-mcp.sh --env-file ~/.config/jira/.env

# Register per-project instead of globally
../mcp/setup-jira-mcp.sh --scope project --env-file ~/.config/jira/.env

# Remove the registration
../mcp/setup-jira-mcp.sh --uninstall
```

The `.env` file (or exported env vars) must provide:

```
JIRA_URL=https://yourcompany.atlassian.net
JIRA_USERNAME=you@yourcompany.com
JIRA_PASSWORD=<API token>
```

Optional: `JIRA_BEARER_TOKEN`, `JIRA_AUTH` (`basic`/`bearer`),
`JIRA_CLOUD` (`false` for Server/Data Center).

## Domain context (optional)

The skill can ground its questions and ticket descriptions in local
domain-context repos: each one a `CONTEXT.md` entry point (read in full)
plus a `docs/` folder of detail files (opened only as needed, per
`CONTEXT.md`'s own file map).

```
export JIRA_REFINE_CONTEXT_DIRS=/path/to/context-a:/path/to/context-b
```

If unset, there's simply no domain context — the skill does less
domain-aware refinement without it.

## Source repos (optional, read reactively)

```
export JIRA_REFINE_SOURCE_REPOS=/home/you/repos/github.com/your-org
```

A directory of real source-code checkouts as subdirectories (e.g.
`service-a/`, `service-b/`). Unlike the context docs above, the skill
never scans this upfront — it only reaches in when a specific question
during the interview names a concrete system the curated context doesn't
already settle.
