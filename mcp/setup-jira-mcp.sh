#!/usr/bin/env bash
# Register (or remove) the jira-mcp MCP server with Claude Code.
#
# Usage:
#   ./setup-jira-mcp.sh                          # register globally (user scope)
#   ./setup-jira-mcp.sh --env-file /path/.env    # pass a credentials file to the server
#   ./setup-jira-mcp.sh --scope project          # register in project scope (.mcp.json)
#   ./setup-jira-mcp.sh --uninstall              # remove the registration
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

set -euo pipefail

# pwd -P resolves symlinks so this works even when the script is reached via a
# symlinked skills directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MCP_SCRIPT="$SCRIPT_DIR/jira_mcp.py"
MCP_NAME="jira-mcp"

ENV_FILE=""
SCOPE="user"
UNINSTALL=0

usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,2\}//' >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage ;;
    *) echo "error: unknown argument: $1" >&2; usage ;;
  esac
done

command -v claude >/dev/null 2>&1 || { echo "error: 'claude' CLI not found — install Claude Code first" >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "error: 'uv' not found — install it from https://docs.astral.sh/uv/" >&2; exit 1; }
[[ -f "$MCP_SCRIPT" ]] || { echo "error: jira_mcp.py not found at $MCP_SCRIPT" >&2; exit 1; }

SCOPE_FLAG=()
[[ "$SCOPE" == "project" ]] && SCOPE_FLAG=("--project")

if [[ $UNINSTALL -eq 1 ]]; then
  if claude mcp remove "$MCP_NAME" "${SCOPE_FLAG[@]}" 2>/dev/null; then
    echo "Removed $MCP_NAME from $SCOPE scope."
  else
    echo "Nothing to remove (not registered in $SCOPE scope)."
  fi
  exit 0
fi

# Build the server command args
SERVER_ARGS=("uv" "run" "-s" "$MCP_SCRIPT")
if [[ -n "$ENV_FILE" ]]; then
  ABS_ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
  [[ -f "$ABS_ENV_FILE" ]] || { echo "error: env file not found: $ABS_ENV_FILE" >&2; exit 1; }
  SERVER_ARGS+=("--env-file" "$ABS_ENV_FILE")
fi

echo "Registering $MCP_NAME (scope: $SCOPE)..."
claude mcp add "$MCP_NAME" "${SCOPE_FLAG[@]}" -- "${SERVER_ARGS[@]}"

echo
echo "Done. Start a new Claude Code session to pick up the $MCP_NAME server."
if [[ -z "$ENV_FILE" ]]; then
  echo
  echo "Tip: pass --env-file /path/to/.env to supply Jira credentials:"
  echo "  $0 --env-file ~/.config/jira/.env"
fi
