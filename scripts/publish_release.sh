#!/usr/bin/env bash
# Tag a shipped version and publish it as a GitHub pre-release.
# Run on up-to-date main immediately after the release PR is merged.
# Usage: scripts/publish_release.sh <version> [--dry-run]
set -euo pipefail

REPO="ttlequals0/MinusPod"
VERSION="${1:?usage: publish_release.sh <version> [--dry-run]}"
DRY_RUN="${2:-}"

if [ -n "$DRY_RUN" ] && [ "$DRY_RUN" != "--dry-run" ]; then
  echo "unknown argument: $DRY_RUN (expected --dry-run)" >&2; exit 1
fi
[ "$#" -le 2 ] || { echo "too many arguments" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "DRY-RUN: $*"
  else
    "$@"
  fi
}

BRANCH=$(git branch --show-current)
[ "$BRANCH" = "main" ] || { echo "must run on main (current: $BRANCH)" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 1; }

git fetch origin main --quiet
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
  || { echo "HEAD is not origin/main; pull first" >&2; exit 1; }

FILE_VERSION=$(python3 -c "import re; print(re.search(r'\"([^\"]+)\"', open('version.py').read()).group(1))")
[ "$FILE_VERSION" = "$VERSION" ] \
  || { echo "version.py has $FILE_VERSION, expected $VERSION" >&2; exit 1; }

git rev-parse "v$VERSION" >/dev/null 2>&1 \
  && { echo "tag v$VERSION already exists" >&2; exit 1; }

NOTES=$(python3 scripts/changelog_section.py "$VERSION")

run git tag -a "v$VERSION" -m "Release $VERSION"
run git push origin "v$VERSION"
if [ "$DRY_RUN" = "--dry-run" ]; then
  echo "DRY-RUN: gh release create v$VERSION --repo $REPO --prerelease --title $VERSION --notes <changelog section>"
else
  gh release create "v$VERSION" --repo "$REPO" --prerelease \
    --title "$VERSION" --notes "$NOTES"
fi
echo "Published pre-release v$VERSION."
