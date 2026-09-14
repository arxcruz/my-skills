# my-skills

Personal collection of portable agent skills (Claude Code, opencode, etc), growing over time.

## Install

From a local checkout:

```
./install.sh --list                # show installable skill ids
./install.sh <skill-id>             # symlink into all known agent skill dirs
./install.sh all                    # install every skill in this repo
./install.sh <skill-id> --agent claude
./install.sh <skill-id> --agent opencode --project /path/to/repo
./install.sh <skill-id> --target /path/to/any/other/tools/skills/dir
./install.sh <skill-id> --uninstall
```

Or straight from GitHub, no clone needed:

```
curl -fsSL https://raw.githubusercontent.com/arxcruz/my-skills/master/install.sh | bash -s -- all
```

This clones (or fast-forward pulls, on repeat runs) the repo into
`~/.local/share/my-skills` and installs from there, so the same options
above (`--agent`, `--target`, `--uninstall`, ...) work after `--`.

See `install.sh --help` for all options. Each skill also documents itself
in its own `README.md`.

## Skills

- [jira-refine](jira-refine/) — refine a Jira ticket into a parallelizable epic/story/task/spike breakdown.
