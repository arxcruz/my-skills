#!/usr/bin/env bash
# Install (or update) any skill in this repo, for any agent tool.
#
# Known agents and where they look for skills:
#   claude    global:  ~/.claude/skills/<id>
#             project: <dir>/.claude/skills/<id>
#   opencode  global:  ~/.config/opencode/skills/<id>  (respects XDG_CONFIG_HOME)
#             project: <dir>/.opencode/skills/<id>
#
# For any other tool, pass --target DIR to symlink/copy straight into its
# skills directory — no agent-specific knowledge needed.
#
# Usage:
#   ./install.sh <skill-id> [--agent claude|opencode|all] [--project DIR]
#   ./install.sh <skill-id> --target DIR
#   ./install.sh <skill-id> [options] --copy         # copy instead of symlink
#   ./install.sh <skill-id> [options] --force         # overwrite a non-symlink target
#   ./install.sh <skill-id> [options] --uninstall
#   ./install.sh --list                                # list installable skill ids
#
# Default: --agent all, global scope, symlink (so pulling this repo picks up
# changes immediately, no re-install step).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  grep -E '^#( |$)' "$0" | sed -E 's/^# ?//' | sed -n '2,20p'
  exit 1
}

list_skills() {
  find "$REPO_DIR" -maxdepth 2 -name SKILL.md -exec dirname {} \; | xargs -n1 basename | sort
}

if [[ "${1:-}" == "--list" ]]; then
  list_skills
  exit 0
fi

[[ $# -lt 1 ]] && usage

SKILL_ID="$1"
shift

SKILL_SRC="$REPO_DIR/$SKILL_ID"
if [[ ! -f "$SKILL_SRC/SKILL.md" ]]; then
  echo "error: no SKILL.md found at $SKILL_SRC" >&2
  echo "Available skills:" >&2
  list_skills >&2
  exit 1
fi

AGENT="all"
PROJECT_DIR=""
TARGET_DIR=""
LINK=1
FORCE=0
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      AGENT="${2:-}"
      [[ -z "$AGENT" ]] && { echo "error: --agent requires a value" >&2; exit 1; }
      shift 2
      ;;
    --project)
      PROJECT_DIR="${2:-}"
      [[ -z "$PROJECT_DIR" ]] && { echo "error: --project requires a directory" >&2; exit 1; }
      shift 2
      ;;
    --target)
      TARGET_DIR="${2:-}"
      [[ -z "$TARGET_DIR" ]] && { echo "error: --target requires a directory" >&2; exit 1; }
      shift 2
      ;;
    --copy) LINK=0; shift ;;
    --force) FORCE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage ;;
    *) echo "error: unknown argument: $1" >&2; usage ;;
  esac
done

# Resolve one or more skills-dir targets to install into.
targets=()

if [[ -n "$TARGET_DIR" ]]; then
  targets+=("$TARGET_DIR")
else
  resolve_agent_dir() {
    local agent="$1"
    case "$agent" in
      claude)
        if [[ -n "$PROJECT_DIR" ]]; then
          echo "$(cd "$PROJECT_DIR" && pwd)/.claude/skills"
        else
          echo "$HOME/.claude/skills"
        fi
        ;;
      opencode)
        if [[ -n "$PROJECT_DIR" ]]; then
          echo "$(cd "$PROJECT_DIR" && pwd)/.opencode/skills"
        else
          echo "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills"
        fi
        ;;
      *)
        echo "error: unknown agent '$agent' (known: claude, opencode; use --target DIR for anything else)" >&2
        exit 1
        ;;
    esac
  }

  if [[ "$AGENT" == "all" ]]; then
    targets+=("$(resolve_agent_dir claude)")
    targets+=("$(resolve_agent_dir opencode)")
  else
    targets+=("$(resolve_agent_dir "$AGENT")")
  fi
fi

for skills_dir in "${targets[@]}"; do
  dest="$skills_dir/$SKILL_ID"

  if [[ $UNINSTALL -eq 1 ]]; then
    if [[ -e "$dest" || -L "$dest" ]]; then
      rm -rf "$dest"
      echo "Removed $dest"
    else
      echo "Nothing installed at $dest"
    fi
    continue
  fi

  mkdir -p "$skills_dir"

  if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ -L "$dest" ]]; then
      rm "$dest"
    elif [[ $FORCE -eq 1 ]]; then
      rm -rf "$dest"
    else
      echo "error: $dest already exists and is not a symlink managed by this script." >&2
      echo "       Re-run with --force to overwrite it, or remove it manually." >&2
      exit 1
    fi
  fi

  if [[ $LINK -eq 1 ]]; then
    ln -s "$SKILL_SRC" "$dest"
    echo "Linked $dest -> $SKILL_SRC"
  else
    cp -R "$SKILL_SRC" "$dest"
    echo "Copied $SKILL_SRC -> $dest"
  fi
done

[[ $UNINSTALL -eq 0 ]] && echo && echo "Restart your agent tool (or start a new session) to pick it up."
