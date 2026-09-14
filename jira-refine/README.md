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

For any tool not built in, point it at this folder the way it expects
skills/prompts to be supplied — most agent CLIs just need `SKILL.md` fed in
as a system/context file and the working directory set here so
`python3 scripts/jira_client.py` resolves. No build step: the script is
stdlib-only Python 3.7+.

## Required environment

```
export JIRA_URL=https://yourcompany.atlassian.net
export JIRA_TOKEN=<API token, or PAT for Server/Data Center>
export JIRA_EMAIL=you@yourcompany.com   # required for Jira Cloud (Basic auth); omit for Server/Data Center (Bearer/PAT)
```

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
already settle ("how does service-a handle X"), matches that name to a
subdirectory, and searches inside just that repo for the specific term.

## Manual script usage

```
python3 scripts/jira_client.py get TICKET-123
python3 scripts/jira_client.py links TICKET-123
python3 scripts/jira_client.py children TICKET-123
python3 scripts/jira_client.py search 'project = ABC AND status = Open'
```

Every command prints one JSON document to stdout and exits 1 with
`{"error": "..."}` on failure — never a raw traceback.
