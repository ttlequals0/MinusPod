# Releasing

MinusPod ships on two channels from the same version line.

| Channel | Docker tags | What it is |
|---------|-------------|------------|
| Edge    | `latest`, `<version>`, `<version>-cpu`, `cpu` | Every merged release, several per day at times |
| Stable  | `stable` (GPU), `stable-cpu` (CPU multi-arch) | An edge release that has soaked in production and been promoted |

Every release is tagged `v<version>` and published as a GitHub
pre-release whose notes are that version's CHANGELOG.md section. The
[releases page](https://github.com/ttlequals0/MinusPod/releases) shows
the full stream; stable releases are the non-pre-release entries and
carry curated, operator-facing notes.

## Per-release flow (maintainer)

1. Merge the release PR to main (squash, subject `Short description
   (X.Y.Z) (#PR)`).
2. Build and push the GPU image; dispatch the CPU workflow
   (`gh workflow run cpu-image.yml -f version=X.Y.Z`).
3. On up-to-date main: `scripts/publish_release.sh X.Y.Z`. This creates
   the annotated tag and the GitHub pre-release.

## Promotion to stable (maintainer)

Promote a release once it has soaked: at least 48 hours running in
production, a clean error-log scan, and no open regression reports
against it.

1. Write curated notes covering everything since the previous stable
   (grouped Breaking changes, New features, Fixes, Upgrade notes) and
   apply them to the release body.
2. `scripts/promote_release.sh X.Y.Z`. This flips the pre-release flag
   and moves the `stable` and `stable-cpu` Docker tags with
   `docker buildx imagetools create` (no rebuild; the CPU multi-arch
   manifest is preserved).

## Changelog conventions

CHANGELOG.md records every version in full technical detail (Keep a
Changelog format). Alongside Added, Changed, Fixed, and Removed, a
**Breaking** section marks anything that requires operator action (env
var renames, compose changes, manual migration steps). Breaking entries
are surfaced at the top of stable release notes.
