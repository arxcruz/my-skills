#!/usr/bin/env bash
# Register (or remove) the jira-mcp MCP server with Claude Code, opencode,
# or antigravity-cli.
#
# Usage:
#   ./setup-jira-mcp.sh                               # register for all agents (user scope)
#   ./setup-jira-mcp.sh --agent claude                # Claude Code only
#   ./setup-jira-mcp.sh --agent opencode              # opencode only
#   ./setup-jira-mcp.sh --agent antigravity           # antigravity-cli only
#   ./setup-jira-mcp.sh --env-file /path/.env         # pass a credentials file to the server
#   ./setup-jira-mcp.sh --scope project               # project scope (uses cwd)
#   ./setup-jira-mcp.sh --project-dir /path/to/proj  # explicit project dir
#   ./setup-jira-mcp.sh --uninstall                   # remove all registrations
#
# To make Jira read-only for one project folder (deny every write tool), see lock-jira-readonly.sh.
#
# Required credentials — set in an env file OR export before running:
#   JIRA_URL        https://yourcompany.atlassian.net
#   JIRA_USERNAME   you@yourcompany.com
#   JIRA_PASSWORD   <API token>
#
# Optional:
#   JIRA_BEARER_TOKEN   OAuth / PAT token (alternative to username+password)
#   JIRA_AUTH           "basic" (default) or "bearer"
#   JIRA_CLOUD          set to "false" for Server/Data Center
#
# Config file locations (user scope):
#   claude      via `claude mcp add/remove`
#   opencode    ~/.config/opencode/config.json  (.mcp.<name>)
#   antigravity ~/.gemini/config/mcp_config.json  (.mcpServers.<name>)
#
# Config file locations (project scope, uses --project-dir or cwd):
#   claude      via `claude mcp add/remove --project`
#   opencode    <dir>/opencode.json  (.mcp.<name>)
#   antigravity <dir>/.agents/mcp_config.json  (.mcpServers.<name>)

set -euo pipefail

# pwd -P resolves symlinks so this works even when called through a symlinked path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MCP_SCRIPT="$SCRIPT_DIR/jira_mcp.py"
MCP_NAME="jira-mcp"

AGENT="all"
ENV_FILE=""
SCOPE="user"
PROJECT_DIR=""
UNINSTALL=0

usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,2\}//' >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      AGENT="${2:-}"
      [[ -z "$AGENT" ]] && { echo "error: --agent requires a value" >&2; exit 1; }
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:-}"
      [[ -z "$ENV_FILE" ]] && { echo "error: --env-file requires a path" >&2; exit 1; }
      shift 2
      ;;
    --scope)
      SCOPE="${2:-}"
      [[ "$SCOPE" != "user" && "$SCOPE" != "project" ]] && { echo "error: --scope must be 'user' or 'project'" >&2; exit 1; }
      shift 2
      ;;
    --project-dir)
      PROJECT_DIR="${2:-}"
      [[ -z "$PROJECT_DIR" ]] && { echo "error: --project-dir requires a path" >&2; exit 1; }
      SCOPE="project"
      shift 2
      ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage ;;
    *) echo "error: unknown argument: $1" >&2; usage ;;
  esac
done

command -v uv >/dev/null 2>&1 || { echo "error: 'uv' not found — install it from https://docs.astral.sh/uv/" >&2; exit 1; }
[[ -f "$MCP_SCRIPT" ]] || { echo "error: jira_mcp.py not found at $MCP_SCRIPT" >&2; exit 1; }

# Resolve project dir for project scope
if [[ "$SCOPE" == "project" && -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(pwd)"
fi
if [[ -n "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
fi

# Resolve env file to an absolute path
if [[ -n "$ENV_FILE" ]]; then
  ABS_ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
  [[ -f "$ABS_ENV_FILE" ]] || { echo "error: env file not found: $ABS_ENV_FILE" >&2; exit 1; }
  ENV_FILE="$ABS_ENV_FILE"
fi

# Build the server command args list (shared across all agents)
SERVER_CMD="uv"
SERVER_ARGS=("run" "-s" "$MCP_SCRIPT")
[[ -n "$ENV_FILE" ]] && SERVER_ARGS+=("--env-file" "$ENV_FILE")

# ---------- per-agent helpers ----------

install_claude() {
  command -v claude >/dev/null 2>&1 || { echo "  [claude] skipped — 'claude' CLI not found" >&2; return; }
  local scope_flag=()
  [[ "$SCOPE" == "project" ]] && scope_flag=("--project")
  if [[ $UNINSTALL -eq 1 ]]; then
    claude mcp remove "$MCP_NAME" "${scope_flag[@]}" 2>/dev/null \
      && echo "  [claude] removed $MCP_NAME ($SCOPE)" \
      || echo "  [claude] nothing to remove"
  else
    claude mcp add "$MCP_NAME" "${scope_flag[@]}" -- "$SERVER_CMD" "${SERVER_ARGS[@]}"
    echo "  [claude] registered $MCP_NAME ($SCOPE)"
  fi
}

# Edit a JSON config file: set or remove .${json_key}.${MCP_NAME}
# Args: <config_file> <json_key> [format: default|opencode]
edit_json_config() {
  local config_file="$1"
  local json_key="$2"
  local format="${3:-default}"
  if [[ $UNINSTALL -eq 1 ]]; then
    [[ -f "$config_file" ]] || return 0
    python3 - "$config_file" "$json_key" "$MCP_NAME" <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
key, name = sys.argv[2], sys.argv[3]
cfg = json.loads(p.read_text())
(cfg.get(key) or {}).pop(name, None)
p.write_text(json.dumps(cfg, indent=2) + "\n")
PYEOF
  else
    local server_json
    server_json="$(python3 -c "
import json, sys
fmt, cmd, args = sys.argv[1], sys.argv[2], sys.argv[3:]
if fmt == 'opencode':
    entry = {'type': 'local', 'command': [cmd] + args, 'enabled': True}
else:
    entry = {'command': cmd, 'args': args}
print(json.dumps(entry))
" "$format" "$SERVER_CMD" "${SERVER_ARGS[@]}")"
    python3 - "$config_file" "$json_key" "$MCP_NAME" "$server_json" <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
key, name, server_json = sys.argv[2], sys.argv[3], sys.argv[4]
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault(key, {})[name] = json.loads(server_json)
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(cfg, indent=2) + "\n")
PYEOF
  fi
}

install_opencode() {
  local config_file
  if [[ "$SCOPE" == "project" ]]; then
    config_file="$PROJECT_DIR/opencode.json"
  else
    config_file="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/config.json"
  fi
  edit_json_config "$config_file" "mcp" opencode
  if [[ $UNINSTALL -eq 1 ]]; then
    echo "  [opencode] removed $MCP_NAME from $config_file"
  else
    echo "  [opencode] registered $MCP_NAME in $config_file"
  fi
}

install_antigravity() {
  local config_file
  if [[ "$SCOPE" == "project" ]]; then
    config_file="$PROJECT_DIR/.agents/mcp_config.json"
  else
    config_file="$HOME/.gemini/config/mcp_config.json"
  fi
  edit_json_config "$config_file" "mcpServers"
  if [[ $UNINSTALL -eq 1 ]]; then
    echo "  [antigravity] removed $MCP_NAME from $config_file"
  else
    echo "  [antigravity] registered $MCP_NAME in $config_file"
  fi
}

# ---------- dispatch ----------

if [[ $UNINSTALL -eq 1 ]]; then
  echo "Removing $MCP_NAME ($SCOPE scope)..."
else
  echo "Registering $MCP_NAME ($SCOPE scope)..."
fi

case "$AGENT" in
  all)
    install_claude
    install_opencode
    install_antigravity
    ;;
  claude)      install_claude ;;
  opencode)    install_opencode ;;
  antigravity) install_antigravity ;;
  *)
    echo "error: unknown agent '$AGENT' (known: claude, opencode, antigravity, all)" >&2
    exit 1
    ;;
esac

echo
if [[ $UNINSTALL -eq 0 ]]; then
  echo "Done. Restart your agent tool (or start a new session) to pick up $MCP_NAME."
  if [[ -z "$ENV_FILE" ]]; then
    echo "Tip: pass --env-file /path/to/.env to supply Jira credentials."
  fi
fi
