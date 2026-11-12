# Releasing

MinusPod ships on two channels from the same version line.

## Contents

- [Per-release flow (maintainer)](#per-release-flow-maintainer)
- [Promotion to stable (maintainer)](#promotion-to-stable-maintainer)
- [Container smoke check (before pushing a built image)](#container-smoke-check-before-pushing-a-built-image)
- [Comparing the old and new audio matcher](#comparing-the-old-and-new-audio-matcher)
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

1. Run `scripts/bump_ytdlp.sh` so the image ships the latest yt-dlp release as its floor. Commit the `ytdlp-version.txt` change with the release.
   Run `python3 scripts/security_packages_hash.py --check --resolve`, which also verifies the pinned digest's hash label; a patch-input change needs the security-packages workflow and `scripts/bump_security_packages.sh` before the GPU build. Release builds pull `ttlequals0/minuspod-security-packages`.
2. On the release branch, build the GPU image for `linux/amd64`. Run the
   container smoke check below, scan it, and push the version tag. Stop
   if any GPU step fails.
3. Dispatch the native multi-architecture CPU workflow from the release
   branch: `gh workflow run cpu-image.yml -f version=X.Y.Z --ref <branch>`.
   Watch the `linux/amd64` and `linux/arm64` jobs and the manifest merge.
   CPU and GPU Trivy reports are separate. Record CPU-only findings
   in the PR; they do not block the GPU release.
4. Merge the release PR to main with squash subject `Short description
   (X.Y.Z) (#PR)` only after both images and required PR checks pass.
5. On up-to-date main: `scripts/publish_release.sh X.Y.Z`. This creates
   the annotated tag and the GitHub pre-release.
6. Publishing the pre-release triggers the release-tags workflow
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

## Comparing the old and new audio matcher

Before a release that changes the audio fingerprint matcher, run
`scripts/compare_fingerprint_matchers.py` against a copy of a real
instance's data. It loads the old matcher from git at run time
(`v2.101.34` by default, change it with `--old-ref`), runs it and the
current matcher on every episode that still has a retained original, and
writes `report.md` and `report.json`.

```
PYTHONPATH=src .venv/bin/python scripts/compare_fingerprint_matchers.py \
  --db /path/to/copy/podcast.db --audio-root /path/to/copy/podcasts \
  --out /path/outside/the/repo/report [--limit 50] [--podcast example-podcast] [--redact]
```

The database is opened read-only and the script never uses the network.
It refuses a database inside the live data directory and an output
directory inside the repo. Episode fingerprints are cached under the
output directory, so a rerun is fast. The report names podcast slugs and
episode ids, so keep it out of public issues and PRs, or pass `--redact`
to replace slugs, episode ids and sponsors with stable hashes.

The exit code is the release gate: 0 passes, 2 fails, 1 means an error. The gate fails when the new matcher loses a match the old matcher
made in scope with an old score of 0.70 or more, and `explain_match`
does not name a rejecting gate for it. A loss that does name a gate
(coverage, evidence, similarity, not distinctive) does not fail the run
but appears in the report, and the maintainer decides whether that
reason is acceptable. Old matches scoring 0.65 to 0.70 that were lost are
listed with a blank verdict column and never block.

The remaining sections never change the exit code. They list gained
matches and whether an existing ad marker confirms them, edges that moved
more than one second outside or inside the old span, matches that now
come back in several pieces, and matches with low similarity or coverage
to check by ear.

## Changelog conventions

CHANGELOG.md records every version in full technical detail (Keep a
Changelog format). Alongside Added, Changed, Fixed, and Removed, a
**Breaking** section marks anything that requires operator action (env
var renames, compose changes, manual migration steps). Breaking entries
are surfaced at the top of stable release notes.
