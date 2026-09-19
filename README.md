# my-skills

Personal collection of portable agent skills and MCP servers (Claude Code,
opencode, etc), growing over time.

> [!CAUTION]
> ## VERY IMPORTANT — lock Jira to read-only before you use this
>
> The Jira skills in this repo (`jira-refine`, `jira-refine-ui`) only ever **read** Jira, but the `jira-mcp` server also exposes tools that **write** to it
> (`jira_create_issue`, `jira_add_comment`, `jira_transition_issue`, `jira_set_*`, …). A skill's instructions
> say never to use them, but a model can ignore instructions — a local model once tried `jira_create_issue`
> during a refinement. The only reliable protection is to make those tools unavailable.
>
> **You (not the agent) run this once for every folder you launch the agent from:**
>
> ```bash
> mcp/lock-jira-readonly.sh --dir /path/to/your/refine/folder
> ```
>
> - It denies every Jira write tool in that folder's `.claude/settings.json` (Claude Code) and
>   `opencode.json` (opencode). Reads (`jira_get_issues`, `jira_search`, …) stay allowed.
> - It is **per folder, not global**, so a separate ticket-creating skill can still write when run
>   from a different folder. `--undo` removes exactly what it added.
> - **Restart the agent** afterwards (config is read at startup), and always launch it from that folder.
> - **The agent must not run this script** — it is a permission boundary, not a setup step for the model.
> - Check it worked: `grep -c jira_create_issue /path/to/your/refine/folder/opencode.json` should print
>   `1` (and `.claude/settings.json` likewise if you use Claude Code).
> - If your MCP server is registered under a name other than `jira-mcp`, add `--server <name>` or the
>   rules will not match and **nothing is blocked**.

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

**MCP servers** — register an MCP server with one or more agent tools:

```
./install.sh mcp --list                               # show available MCP servers
./install.sh mcp <name>                               # register for all agents (user scope)
./install.sh mcp <name> --agent claude                # Claude Code only
./install.sh mcp <name> --agent opencode              # opencode only
./install.sh mcp <name> --agent antigravity           # antigravity-cli only
./install.sh mcp <name> --env-file ~/.config/jira/.env
./install.sh mcp <name> --scope project               # project scope (uses cwd)
./install.sh mcp <name> --project-dir /path/to/proj
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

| agent | skills (global) | skills (project) | MCP config (global) | MCP config (project) |
|---|---|---|---|---|
| claude | `~/.claude/skills/<id>` | `<dir>/.claude/skills/<id>` | via `claude mcp add` | via `claude mcp add --project` |
| opencode | `~/.config/opencode/skills/<id>` | `<dir>/.opencode/skills/<id>` | `~/.config/opencode/config.json` (`.mcp`) | `<dir>/opencode.json` (`.mcp`) |
| antigravity | — | — | `~/.gemini/config/mcp_config.json` (`.mcpServers`) | `<dir>/.agents/mcp_config.json` (`.mcpServers`) |

For any other tool, pass `--target DIR` to symlink/copy skills straight into its
skills directory — no agent-specific knowledge needed.

## Skills

- [jira-refine](jira-refine/) — refine a Jira ticket into a parallelizable epic/story/task/spike breakdown, interviewing you in the terminal.
- [jira-refine-ui](jira-refine-ui/) — the same refinement on a local web page: question cards, plan review with size editing, rendered plan file.
- [repo-context](repo-context/) — draft a `CONTEXT.md` domain-context folder for a repo (ownership, layout map, conventions, gaps) that the two skills above discover automatically.

## MCP servers

- [jira-mcp](mcp/jira_mcp.py) — Jira tools (search, get/create/update issues, transitions, comments, sprints).
  Requires [`uv`](https://docs.astral.sh/uv/) and Jira credentials (`JIRA_URL` + `JIRA_USERNAME`/`JIRA_PASSWORD` or `JIRA_BEARER_TOKEN`).
  Install with: `./install.sh mcp jira-mcp --env-file ~/.config/jira/.env`
