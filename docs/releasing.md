# Releasing

MinusPod ships on two channels from the same version line.

## Contents

- [Per-release flow (maintainer)](#per-release-flow-maintainer)
- [Promotion to stable (maintainer)](#promotion-to-stable-maintainer)
- [Container smoke check (before pushing a built image)](#container-smoke-check-before-pushing-a-built-image)
- [Changelog conventions](#changelog-conventions)

| Channel | Docker tags | What it is |
|---------|-------------|------------|
| Edge    | `latest`, `<version>`, `<version>-cpu`, `cpu` | Every merged release, several per day at times |
| Stable  | `stable` (GPU), `stable-cpu` (CPU multi-arch) | An edge release that has soaked in production and been promoted |

Every release is tagged `v<version>` and published as a GitHub
pre-release whose notes cover every CHANGELOG.md section shipped since
the previous release. Usually that is one section; when a PR bumped the
version more than once before merging, the notes roll up all of its
sections so nothing ships undocumented. The
[releases page](https://github.com/ttlequals0/MinusPod/releases) shows
the full stream; stable releases are the non-pre-release entries and
carry curated, operator-facing notes.

## Per-release flow (maintainer)

1. On the release branch, build the GPU image for `linux/amd64`. Run the
   container smoke check below, scan it, and push the version tag. Stop
   if any GPU step fails.
2. Dispatch the native multi-architecture CPU workflow from the release
   branch: `gh workflow run cpu-image.yml -f version=X.Y.Z --ref <branch>`.
   Watch the `linux/amd64` and `linux/arm64` jobs and the manifest merge.
   CPU and GPU Trivy reports are separate. Record CPU-only findings
   in the PR; they do not block the GPU release.
3. Merge the release PR to main with squash subject `Short description
   (X.Y.Z) (#PR)` only after both images and required PR checks pass.
4. On up-to-date main: `scripts/publish_release.sh X.Y.Z`. This creates
   the annotated tag and the GitHub pre-release.
5. Publishing the pre-release triggers the release-tags workflow
   (`.github/workflows/release-tags.yml`), which moves `latest` to the
   new GPU image and `cpu` to the new CPU image (waiting up to 10
   minutes for the CPU build to finish). The workflow skips itself when
   the published release is not the newest one, so retroactively
   publishing an old version never moves the edge tags backwards.

The GPU image is amd64-only. The CPU workflow publishes one manifest for
amd64 and arm64 on native GitHub runners. Use `promote_cpu_tag=true` only
for a manual, out-of-band move of `:cpu`; the normal release flow moves it
from the published pre-release.

## Promotion to stable (maintainer)

Promote a release once it has soaked: at least 48 hours running in
production, a clean error-log scan, and no open regression reports
against it.

1. Write curated notes covering everything since the previous stable
   (grouped Breaking changes, New features, Fixes, Upgrade notes) and
   apply them to the release body.
2. `scripts/promote_release.sh X.Y.Z`. This moves the `stable` and
   `stable-cpu` Docker tags with `docker buildx imagetools create` (no
   rebuild; the CPU multi-arch manifest is preserved), then flips the
   pre-release flag.

## Container smoke check (before pushing a built image)

The test suite runs with the repo root on `PYTHONPATH`, but gunicorn's
boot path inside the container only has `/app/src` on `sys.path` (see
`gunicorn.conf.py`). A module-level import that only resolves with the
repo root on the path passes the test suite but crash-loops every
worker in the container. `tests/unit/test_container_import_paths.py`
catches this in CI by reproducing the narrower boot path in a
subprocess, but a build-time check on the real image closes the gap
between "the test passed" and "gunicorn actually boots in the built
image."

After building an image locally and before pushing it, run:

```
docker run --rm --entrypoint python <image> -c "import sys; sys.path.insert(0,'/app/src'); import database, main_app"
```

If this fails, do not push the image. Investigate the import error (it
is almost always a module-level import that only resolves when the
repo root is on `sys.path`) and fix it before rebuilding.

## Changelog conventions

CHANGELOG.md records every version in full technical detail (Keep a
Changelog format). Alongside Added, Changed, Fixed, and Removed, a
**Breaking** section marks anything that requires operator action (env
var renames, compose changes, manual migration steps). Breaking entries
are surfaced at the top of stable release notes.
