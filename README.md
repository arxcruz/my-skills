# my-skills

Personal collection of portable agent skills (Claude Code, opencode, etc), growing over time.

## Install

```
./install.sh --list                # show installable skill ids
./install.sh <skill-id>             # symlink into all known agent skill dirs
./install.sh <skill-id> --agent claude
./install.sh <skill-id> --agent opencode --project /path/to/repo
./install.sh <skill-id> --target /path/to/any/other/tools/skills/dir
./install.sh <skill-id> --uninstall
```

See `install.sh --help` for all options. Each skill also documents itself
in its own `README.md`.

## Skills

- [jira-refine](jira-refine/) — refine a Jira ticket into a parallelizable epic/story/task/spike breakdown.
