# jira-refine-ui

The web-UI version of [`jira-refine`](../jira-refine/): a portable skill for
refining a Jira ticket into a parallelizable epic / story / task / spike
breakdown, with the interview and plan review on a local browser page. Prefer
the terminal? Use [`jira-refine`](../jira-refine/) instead. See `SKILL.md` for the full instructions —
it's the file any agent tool actually reads.

> [!CAUTION]
> ## VERY IMPORTANT — lock Jira to read-only before you use this
>
> These skills only ever **read** Jira, but the `jira-mcp` server also exposes tools that **write** to it
> (`jira_create_issue`, `jira_add_comment`, `jira_transition_issue`, `jira_set_*`, …). A skill's instructions
> say never to use them, but a model can ignore instructions — a local model once tried `jira_create_issue`
> during a refinement. The only reliable protection is to make those tools unavailable.
>
> **You (not the agent) run this once for every folder you launch the agent from:**
>
> ```bash
> ../mcp/lock-jira-readonly.sh --dir /path/to/your/refine/folder
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

From the repo root, use the generic installer (see [`../install.sh`](../install.sh)):

```
../install.sh jira-refine-ui                 # symlink into ~/.claude/skills and ~/.config/opencode/skills
../install.sh jira-refine-ui --agent claude  # just Claude Code
../install.sh jira-refine-ui --target /path/to/some/other/tools/skills/dir
```

## How it works

The skill always runs in web mode. It starts a local server (`server.mjs`, Node 20+, no dependencies) bound to
127.0.0.1 and gives you a page with the 8-topic coverage checklist, question
cards (options, recommendation, discussion thread, defer/reopen), and a plan
tab where you edit Fibonacci sizes, comment per item, request changes or
approve. Everything is staged until you press **Send to Agent**. Details for
the agent are in [`WEB.md`](WEB.md); session state lives in
`.jira-refine/` in the working directory (consider gitignoring it).

When the plan is approved and written, the page adds a **Plan file** tab that
renders the finished Markdown (tables, lists, code) in place.

**opencode:** start it with a server port so the page can push each Send
straight into the agent's session (no polling, no waiting):

```
opencode --port 4096        # or: alias oc='opencode --port 4096'
```

The web server finds it at `http://127.0.0.1:4096` (override with
`OPENCODE_URL` / `--opencode-url`; `OPENCODE_SERVER_PASSWORD` is honoured).
Without it the skill falls back to a polling wait loop, which is slower and
can stall. The page tells you when a Send isn't being picked up.

Inspired by [grill-with-ui](https://github.com/jasonku09/grill-with-ui).

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

## Domain context (optional, automatic)

The skill grounds its questions in short per-repo `CONTEXT.md` files (purpose,
ownership, layout, conventions, known gaps). Generate one with the
[`repo-context`](../repo-context/) skill; it lands in
`~/.local/share/jira-refine/context/<repo>/` and is discovered automatically —
no configuration. The skill first reads only each file's header, then opens in
full just the repos the ticket touches.

Optionally, to also search other folders (e.g. a team-shared context repo):

```
export JIRA_REFINE_CONTEXT_DIRS=/path/to/context-a:/path/to/context-b
```

With no context anywhere, the skill simply does less domain-aware refinement.

## Source repos (optional, read reactively)

```
export JIRA_REFINE_SOURCE_REPOS=/home/you/repos/github.com/your-org
```

A directory of real source-code checkouts as subdirectories (e.g.
`service-a/`, `service-b/`). Unlike the context docs above, the skill
never scans this upfront — it only reaches in when a specific question
during the interview names a concrete system the curated context doesn't
already settle.
