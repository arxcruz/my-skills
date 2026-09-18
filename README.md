# my-skills

Personal collection of portable agent skills and MCP servers (Claude Code,
opencode, etc), growing over time.

## Install

One script handles both skills and MCP servers.

**Skills** — symlink a skill into the agent's skills path:

```
./install.sh --list                        # show skills and MCP servers
./install.sh <skill-id>                    # symlink into all known agent skill dirs
./install.sh all                           # install every skill in this repo
./install.sh <skill-id> --agent claude
./install.sh <skill-id> --agent opencode --project /path/to/repo
./install.sh <skill-id> --target /path/to/any/tools/skills/dir
./install.sh <skill-id> --uninstall
```

**MCP servers** — register an MCP server with Claude Code:

```
./install.sh mcp --list                    # show available MCP servers
./install.sh mcp <name>                    # register globally (user scope)
./install.sh mcp <name> --env-file ~/.config/jira/.env
./install.sh mcp <name> --scope project    # project scope (.mcp.json)
./install.sh mcp <name> --uninstall
```

Or straight from GitHub, no clone needed (skills only):

```
curl -fsSL https://raw.githubusercontent.com/arxcruz/my-skills/main/install.sh | bash -s -- all
```

This clones (or fast-forward pulls, on repeat runs) the repo into
`~/.local/share/my-skills` and installs from there, so the same options work
after `--`.

### Known agents

| agent | skills (global) | skills (project) |
|---|---|---|
| claude | `~/.claude/skills/<id>` | `<dir>/.claude/skills/<id>` |
| opencode | `~/.config/opencode/skills/<id>` | `<dir>/.opencode/skills/<id>` |

For any other tool, pass `--target DIR` to symlink/copy straight into its
skills directory — no agent-specific knowledge needed.

## Skills

- [jira-refine](jira-refine/) — refine a Jira ticket into a parallelizable epic/story/task/spike breakdown.

## MCP servers

- [jira-mcp](mcp/jira_mcp.py) — Jira tools (search, get/create/update issues, transitions, comments, sprints).
  Requires [`uv`](https://docs.astral.sh/uv/) and Jira credentials (`JIRA_URL` + `JIRA_USERNAME`/`JIRA_PASSWORD` or `JIRA_BEARER_TOKEN`).
  Install with: `./install.sh mcp jira-mcp --env-file ~/.config/jira/.env`
