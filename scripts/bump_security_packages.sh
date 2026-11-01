#!/usr/bin/env bash
# Pin the app Dockerfiles to the published security packages image for the current patch inputs.
# Usage: scripts/bump_security_packages.sh [tag]   (default tag: the current inputs hash)
set -euo pipefail
[ "$#" -le 1 ] || { echo "usage: scripts/bump_security_packages.sh [tag]" >&2; exit 1; }
cd "$(dirname "$0")/.."

REPO=ttlequals0/minuspod-security-packages
HASH=$(python3 scripts/security_packages_hash.py)
TAG=${1:-$HASH}

DIGEST=$(docker buildx imagetools inspect "$REPO:$TAG" --format '{{json .Manifest.Digest}}' | tr -d '"') || {
    echo "Tag $REPO:$TAG not found. Open or update the PR so the security-packages workflow publishes this hash." >&2
    exit 1
}
[ -n "$DIGEST" ] || { echo "Empty digest for $REPO:$HASH" >&2; exit 1; }

python3 scripts/security_packages_hash.py --pin "$REPO@$DIGEST"
