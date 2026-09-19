#!/usr/bin/env bash
# Block every Jira *write* tool of the jira-mcp server for one project folder, so an agent working
# there (e.g. the jira-refine skills) physically cannot create/edit/transition tickets, even if a
# model ignores the "read-only" instruction in a skill. Global agent config is left untouched, so a
# separate ticket-creating skill can still write when run from another folder.
#
# The list of write tools is derived from jira_mcp.py: every `jira_*` tool except the read-only ones
# (jira_get_*, jira_search, jira_connection_health, jira_debug_fields, jira_download_attachment).
#
# Usage:
#   ./lock-jira-readonly.sh                       # lock the current directory, all agents
#   ./lock-jira-readonly.sh --dir /path/to/proj   # another project folder
#   ./lock-jira-readonly.sh --agent claude        # or: opencode | all (default)
#   ./lock-jira-readonly.sh --server jira-mcp     # MCP server name (default: jira-mcp)
#   ./lock-jira-readonly.sh --dry-run             # show what would be written
#   ./lock-jira-readonly.sh --undo                # remove exactly the rules this script added
#
# Where the rules go (merged into existing files, other settings preserved):
#   claude    <dir>/.claude/settings.json   permissions.deny  ["mcp__<server>__<tool>", ...]
#   opencode  <dir>/opencode.json           permission        {"<server>_<tool>": "deny", ...}

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$PWD"
AGENT="all"
SERVER="jira-mcp"
DRY=0
UNDO=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)     DIR="${2:?--dir requires a path}"; shift 2 ;;
    --agent)   AGENT="${2:?--agent requires claude|opencode|all}"; shift 2 ;;
    --server)  SERVER="${2:?--server requires a name}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --undo)    UNDO=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; exit 1 ;;
  esac
done
case "$AGENT" in claude|opencode|all) ;; *) echo "error: --agent must be claude, opencode or all" >&2; exit 1 ;; esac
[[ -d "$DIR" ]] || { echo "error: no such directory: $DIR" >&2; exit 1; }
DIR="$(cd "$DIR" && pwd)"
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }

DIR="$DIR" AGENT="$AGENT" SERVER="$SERVER" DRY="$DRY" UNDO="$UNDO" MCP_PY="$HERE/jira_mcp.py" python3 - <<'PY'
import json, os, re, sys

d, agent, server = os.environ["DIR"], os.environ["AGENT"], os.environ["SERVER"]
dry, undo = os.environ["DRY"] == "1", os.environ["UNDO"] == "1"

src = open(os.environ["MCP_PY"]).read()
tools = sorted(set(re.findall(r"^(?:async )?def (jira_\w+)\(", src, re.M)))
READ = re.compile(r"^jira_(get_|search$|connection_health$|debug_fields$|download_attachment$)")
write = [t for t in tools if not READ.match(t)]
if not write:
    sys.exit("error: found no write tools in jira_mcp.py")

def load(path):
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path))
    except Exception as e:
        sys.exit(f"error: {path} is not plain JSON ({e}); edit it by hand or use --agent to skip it")

def save(path, data):
    if dry:
        print(f"[dry-run] would write {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)

def claude():
    path = os.path.join(d, ".claude", "settings.json")
    data = load(path)
    names = [f"mcp__{server}__{t}" for t in write]
    deny = data.setdefault("permissions", {}).setdefault("deny", [])
    if undo:
        data["permissions"]["deny"] = [x for x in deny if x not in names]
        if not data["permissions"]["deny"]: del data["permissions"]["deny"]
        if not data["permissions"]: del data["permissions"]
    else:
        for n in names:
            if n not in deny: deny.append(n)
    save(path, data)
    print(f"claude:   {'removed' if undo else 'denied'} {len(names)} write tools -> {path}")

def opencode():
    path = os.path.join(d, "opencode.json")
    data = load(path)
    keys = [f"{server}_{t}" for t in write]
    perm = data.get("permission")
    if perm is not None and not isinstance(perm, dict):
        sys.exit(f"error: {path} has a non-object 'permission' ({perm!r}); edit it by hand")
    perm = data.setdefault("permission", {})
    if undo:
        for k in keys:
            if perm.get(k) == "deny": del perm[k]
        if not perm: del data["permission"]
    else:
        for k in keys: perm[k] = "deny"
        data.setdefault("$schema", "https://opencode.ai/config.json")
    save(path, data)
    print(f"opencode: {'removed' if undo else 'denied'} {len(keys)} write tools -> {path}")

if agent in ("claude", "all"): claude()
if agent in ("opencode", "all"): opencode()
print("read-only tools left available: " + ", ".join(t for t in tools if READ.match(t)))
PY
