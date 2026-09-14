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
# Usage (run locally from a checkout, or piped straight from GitHub):
#   ./install.sh <skill-id> [--agent claude|opencode|all] [--project DIR]
#   ./install.sh all                                   # every skill in the repo
#   ./install.sh <skill-id> --target DIR
#   ./install.sh <skill-id> [options] --copy         # copy instead of symlink
#   ./install.sh <skill-id> [options] --force         # overwrite a non-symlink target
#   ./install.sh <skill-id> [options] --uninstall
#   ./install.sh --list                                # list installable skill ids
#
#   curl -fsSL https://raw.githubusercontent.com/arxcruz/my-skills/main/install.sh | bash -s -- all
#
# When not run from inside a checkout of this repo (e.g. the curl|bash form
# above), it clones (or fast-forward pulls, if already cloned before) this
# repo into a cache dir and installs from there instead, so the same symlink
# stays live across future `git pull`s of that cache.
#
# Default: --agent all, global scope, symlink (so pulling this repo picks up
# changes immediately, no re-install step).

set -euo pipefail

REPO_URL="https://github.com/arxcruz/my-skills.git"
CACHE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/my-skills"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || true)"

if [[ -n "$SCRIPT_DIR" && -d "$SCRIPT_DIR/.git" ]]; then
  REPO_DIR="$SCRIPT_DIR"
else
  command -v git >/dev/null 2>&1 || { echo "error: git is required to install from a remote (curl|bash) invocation" >&2; exit 1; }
  if [[ -d "$CACHE_DIR/.git" ]]; then
    git -C "$CACHE_DIR" pull -q --ff-only
  else
    git clone -q "$REPO_URL" "$CACHE_DIR"
  fi
  REPO_DIR="$CACHE_DIR"
fi

usage() {
  cat >&2 <<'EOF'
Usage:
  ./install.sh <skill-id> [--agent claude|opencode|all] [--project DIR]
  ./install.sh all                                   # every skill in the repo
  ./install.sh <skill-id> --target DIR
  ./install.sh <skill-id> [options] --copy
  ./install.sh <skill-id> [options] --force
  ./install.sh <skill-id> [options] --uninstall
  ./install.sh --list

  curl -fsSL https://raw.githubusercontent.com/arxcruz/my-skills/main/install.sh | bash -s -- all
EOF
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

if [[ "$SKILL_ID" == "all" ]]; then
  SKILL_IDS=()
  while IFS= read -r id; do SKILL_IDS+=("$id"); done < <(list_skills)
else
  SKILL_SRC="$REPO_DIR/$SKILL_ID"
  if [[ ! -f "$SKILL_SRC/SKILL.md" ]]; then
    echo "error: no SKILL.md found at $SKILL_SRC" >&2
    echo "Available skills:" >&2
    list_skills >&2
    exit 1
  fi
  SKILL_IDS=("$SKILL_ID")
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

for id in "${SKILL_IDS[@]}"; do
  src="$REPO_DIR/$id"

  for skills_dir in "${targets[@]}"; do
    dest="$skills_dir/$id"

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
      ln -s "$src" "$dest"
      echo "Linked $dest -> $src"
    else
      cp -R "$src" "$dest"
      echo "Copied $src -> $dest"
    fi
  done
done

[[ $UNINSTALL -eq 0 ]] && echo && echo "Restart your agent tool (or start a new session) to pick it up."
