#!/usr/bin/env bash
# Pin the app Dockerfiles to the published security packages image for the current patch inputs.
# Usage: scripts/bump_security_packages.sh
set -euo pipefail
[ "$#" -eq 0 ] || { echo "usage: scripts/bump_security_packages.sh" >&2; exit 1; }
cd "$(dirname "$0")/.."

REPO=ttlequals0/minuspod-security-packages
HASH=$(python3 scripts/security_packages_hash.py)

DIGEST=$(docker buildx imagetools inspect "$REPO:$HASH" --format '{{json .Manifest.Digest}}' | tr -d '"') || {
    echo "Tag $REPO:$HASH not found. Open or update the PR so the security-packages workflow publishes this hash." >&2
    exit 1
}
[ -n "$DIGEST" ] || { echo "Empty digest for $REPO:$HASH" >&2; exit 1; }

python3 scripts/security_packages_hash.py --pin "$REPO@$DIGEST"
