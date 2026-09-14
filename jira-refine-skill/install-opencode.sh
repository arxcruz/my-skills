#!/usr/bin/env bash
# Install (or update) the jira-refine skill for opencode.
#
# opencode discovers skills from, in order of scope:
#   global:  ~/.config/opencode/skills/<id>/SKILL.md
#   project: <repo>/.opencode/skills/<id>/SKILL.md
#
# By default this installs globally, as a symlink back to this repo, so
# editing SKILL.md or scripts/jira_client.py here is picked up immediately
# with no re-install step.
#
# Usage:
#   ./install-opencode.sh                  # global install (symlink)
#   ./install-opencode.sh --project DIR     # install into DIR/.opencode/skills instead
#   ./install-opencode.sh --copy            # copy files instead of symlinking
#   ./install-opencode.sh --force           # overwrite a non-symlink target
#   ./install-opencode.sh --uninstall        # remove the global install
#   ./install-opencode.sh --project DIR --uninstall   # remove the project install

set -euo pipefail

SKILL_ID="jira-refine"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="copy_or_link"
TARGET_KIND="global"
PROJECT_DIR=""
FORCE=0
UNINSTALL=0
LINK=1

usage() {
  grep -E '^#( |$)' "$0" | sed -E 's/^# ?//' | sed -n '2,20p'
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      TARGET_KIND="project"
      PROJECT_DIR="${2:-}"
      [[ -z "$PROJECT_DIR" ]] && { echo "error: --project requires a directory" >&2; exit 1; }
      shift 2
      ;;
    --copy)
      LINK=0
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [[ "$TARGET_KIND" == "project" ]]; then
  PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
  SKILLS_DIR="$PROJECT_DIR/.opencode/skills"
else
  SKILLS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills"
fi

TARGET="$SKILLS_DIR/$SKILL_ID"

if [[ $UNINSTALL -eq 1 ]]; then
  if [[ -e "$TARGET" || -L "$TARGET" ]]; then
    rm -rf "$TARGET"
    echo "Removed $TARGET"
  else
    echo "Nothing installed at $TARGET"
  fi
  exit 0
fi

command -v python3 >/dev/null 2>&1 || {
  echo "warning: python3 not found on PATH — the skill's jira_client.py will not run" >&2
}

mkdir -p "$SKILLS_DIR"

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  if [[ -L "$TARGET" ]]; then
    rm "$TARGET"
  elif [[ $FORCE -eq 1 ]]; then
    rm -rf "$TARGET"
  else
    echo "error: $TARGET already exists and is not a symlink managed by this script." >&2
    echo "       Re-run with --force to overwrite it, or remove it manually." >&2
    exit 1
  fi
fi

if [[ $LINK -eq 1 ]]; then
  ln -s "$SOURCE_DIR" "$TARGET"
  echo "Linked $TARGET -> $SOURCE_DIR"
else
  cp -R "$SOURCE_DIR" "$TARGET"
  echo "Copied $SOURCE_DIR -> $TARGET"
fi

echo
if [[ -z "${JIRA_URL:-}" || -z "${JIRA_TOKEN:-}" ]]; then
  echo "Reminder: export these before opencode runs this skill:"
  echo "  export JIRA_URL=https://yourcompany.atlassian.net"
  echo "  export JIRA_TOKEN=<API token, or PAT for Server/Data Center>"
  echo "  export JIRA_EMAIL=you@yourcompany.com   # required for Jira Cloud"
else
  echo "JIRA_URL and JIRA_TOKEN are already set in this shell."
  if [[ "$JIRA_URL" == *atlassian.net* && -z "${JIRA_EMAIL:-}" ]]; then
    echo "warning: JIRA_URL looks like Jira Cloud but JIRA_EMAIL is not set — requests will 403." >&2
  fi
fi

echo
echo "Installed as opencode skill id: $SKILL_ID"
echo "Restart opencode (or start a new session) to pick it up."
