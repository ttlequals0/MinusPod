#!/usr/bin/env bash
# Promote a soaked pre-release to stable: flip the prerelease flag and
# move the :stable / :stable-cpu Docker tags. Run /release-notes first
# so the release body is the curated stable notes.
# Usage: scripts/promote_release.sh <version> [--dry-run]
set -euo pipefail

REPO="ttlequals0/MinusPod"
IMAGE="ttlequals0/minuspod"
VERSION="${1:?usage: promote_release.sh <version> [--dry-run]}"
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

RELEASE=$(gh api "repos/$REPO/releases/tags/v$VERSION" 2>/dev/null) \
  || { echo "no GitHub release for v$VERSION" >&2; exit 1; }
RELEASE_ID=$(echo "$RELEASE" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
IS_PRE=$(echo "$RELEASE" | python3 -c "import json,sys; print(json.load(sys.stdin)['prerelease'])")
[ "$IS_PRE" = "True" ] \
  || { echo "v$VERSION is not a pre-release (already promoted?)" >&2; exit 1; }

echo "Checking both image variants exist on Docker Hub..."
docker manifest inspect "$IMAGE:$VERSION" >/dev/null
docker manifest inspect "$IMAGE:$VERSION-cpu" >/dev/null

run docker buildx imagetools create -t "$IMAGE:stable" "$IMAGE:$VERSION"
run docker buildx imagetools create -t "$IMAGE:stable-cpu" "$IMAGE:$VERSION-cpu"
run gh api -X PATCH "repos/$REPO/releases/$RELEASE_ID" -F prerelease=false

echo "Promoted v$VERSION to stable."
if [ "$DRY_RUN" != "--dry-run" ]; then
  docker buildx imagetools inspect "$IMAGE:stable-cpu" | head -12 || true
fi
