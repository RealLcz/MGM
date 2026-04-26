#!/usr/bin/env bash
# Copy MendelGM/output_mgm to a staging directory as output_hgm (local output_mgm is unchanged).
# Use this tree to push to GitHub under the name output_hgm/.
#
# Usage:
#   ./scripts/publish_output_as_output_hgm.sh
#   STAGING=/path/to/publish ./scripts/publish_output_as_output_hgm.sh
#   ./scripts/publish_output_as_output_hgm.sh --git-init
#
# After --git-init, add remote and push, e.g.:
#   cd "$STAGING" && git remote add origin git@github.com:YOU/output_hgm.git && git push -u origin main

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="${SOURCE:-$REPO_ROOT/output_mgm}"
STAGING="${STAGING:-/tmp/mendelgm_output_hgm_publish}"
GIT_INIT=0

for arg in "$@"; do
  case "$arg" in
    --git-init) GIT_INIT=1 ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
  esac
done

if [[ ! -d "$SOURCE" ]]; then
  echo "Source not found: $SOURCE" >&2
  exit 1
fi

mkdir -p "$STAGING"
rm -rf "$STAGING/output_hgm"
mkdir -p "$STAGING/output_hgm"

if command -v rsync >/dev/null 2>&1; then
  rsync -a "$SOURCE/" "$STAGING/output_hgm/"
else
  cp -a "$SOURCE/." "$STAGING/output_hgm/"
fi

echo "Copied (unchanged on disk):"
echo "  $SOURCE"
echo "→ $STAGING/output_hgm"

if [[ "$GIT_INIT" -eq 1 ]]; then
  cd "$STAGING"
  if [[ ! -d .git ]]; then
    git init -b main
  fi
  git add output_hgm
  git status
  if git diff --cached --quiet; then
    echo "Nothing to commit (already up to date)." >&2
  else
    git commit -m "Add output_hgm (from output_mgm snapshot)"
  fi
  echo
  echo "Git repo root is: $STAGING"
  echo "(Do not run 'git remote' inside output_hgm/ — stay in the directory above it.)"
  echo "Next:"
  echo "  cd '$STAGING'"
  echo "  git remote add origin YOUR_GITHUB_CLONE_URL"
  echo "  git push -u origin main"
else
  echo
  echo "Tip: pass --git-init to create .git in STAGING before git remote / push."
fi
