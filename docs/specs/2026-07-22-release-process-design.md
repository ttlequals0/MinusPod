# Formal release process and in-app update checks (issue #567)

Date: 2026-07-22
Status: implemented in 2.73.0

## Problem

MinusPod ships several releases per day. Every version goes straight to
`:latest` on Docker Hub. There are no git tags past v2.1.9, no GitHub
Releases, no notion of "stable", and no way for a running instance to
learn that a newer version exists. Issue #567 asks for in-app update
visibility (version date, check button, banner, daily check, changelog
link). The user base is growing and needs a stable channel without
slowing the edge cadence.

## Decisions (agreed in brainstorming)

1. Stable model: promotion. Edge cadence is unchanged; a soaked edge
   version is promoted to stable after the fact.
2. Update source: GitHub Releases API, proxied and cached by the
   backend.
3. Channel publishing: every version gets a GitHub Release marked
   pre-release at ship time; promotion flips it to a full release.
4. In-app UX: all of #567, plus webhook and email notification of new
   versions via the existing notification services.
5. Versioning: keep the current bump-minor-per-release scheme. Add a
   "Breaking" changelog section convention for anything requiring
   operator action.
6. Changelog is two-tier: CHANGELOG.md stays the full-detail dev
   changelog (edge users' view); stable releases get curated,
   operator-facing release notes produced with a dedicated Claude
   skill.

## 1. Channels and publishing flow

| Channel | Docker tags                                       | Meaning                          |
|---------|---------------------------------------------------|----------------------------------|
| Edge    | `:latest`, `:<version>`, `:<version>-cpu`, `:cpu` | Today's flow, unchanged          |
| Stable  | `:stable` (GPU), `:stable-cpu` (CPU multi-arch)   | A promoted, soaked edge version  |

Per-release additions to the existing flow (scripted as
`scripts/publish_release.sh <version>`):

- Create annotated git tag `v<version>` on the merge commit and push it.
- Create a GitHub Release for the tag, marked pre-release, body taken
  verbatim from that version's CHANGELOG.md section.

Promotion (manual, after soak):

- Guideline soak criteria: 48h+ running in production, clean log error scan,
  no open regression reports against the version.
- `/release-notes <version>` skill (see section 3) produces the curated
  release body; after approval it is applied with `gh api`.
- `scripts/promote_release.sh <version>` performs the mechanical half:
  - `gh api` PATCH the release: `prerelease=false`.
  - Move Docker tags without rebuilding:
    `docker buildx imagetools create -t ttlequals0/minuspod:stable
    ttlequals0/minuspod:<version>` and the same for `:stable-cpu` from
    `:<version>-cpu`. imagetools preserves the multi-arch manifest, so
    `cpu-image.yml` needs no changes.

No backfill of tags between v2.1.9 and now, with one bootstrap
exception: the current production version gets a retroactive tag and
pre-release so it can be promoted as the first stable. Every version
after that is tagged at ship time.

## 2. Changelog conventions (two-tier)

- CHANGELOG.md is unchanged in role: every version, full technical
  detail, Keep a Changelog format. It is the edge users' changelog and
  the source for pre-release GitHub Release bodies.
- New section convention: "Breaking" (alongside Added/Fixed/Changed)
  for anything requiring operator action (env var renames, compose
  changes, manual migration steps). The curation skill surfaces these
  at the top of stable notes.
- Stable release notes are a curated rewrite, not a mechanical rollup:
  written for operators, grouped New features / Fixes / Breaking
  changes / Upgrade notes, internal detail dropped, fix-of-a-fix churn
  within the range merged or elided (a bug introduced and fixed inside
  the range disappears entirely).
- Canonical home of curated notes is the GitHub Release body only. No
  second checked-in file. The in-app changelog link points at the
  GitHub releases page.

## 3. Release-notes curation skill

Location: `~/.claude/commands/release-notes.md` (user-level, alongside
the existing build-and-push command; `.claude/` in the repo is
gitignored). Invoked as `/release-notes <version>` at promotion time.

Steps the skill encodes:

1. Determine range: previous stable (newest non-prerelease GitHub
   Release) exclusive, to `<version>` inclusive.
2. Gather material: parse CHANGELOG.md sections in range; map each
   version to its merged PR via squash subjects
   (`Short description (X.Y.Z) (#PR)` on main); pull linked issues
   from PR bodies.
3. Draft curated notes per section 2, each entry linking its PR and
   issue where one exists.
4. Edit the draft for plain, operator-facing wording.
5. Present for approval, then apply the body to the GitHub Release via
   `gh api`.

Because the skill is not versioned with the repo, this spec records the
conventions it depends on: the changelog section format, the squash
subject format, and the soak criteria. The skill can be rebuilt from
this document if lost.

## 4. Update-check backend

New endpoint in `src/api/system.py`:

`GET /api/v1/system/updates`

```json
{
  "current": {"version": "2.72.0", "releaseDate": "2026-07-22"},
  "stable": {"version": "2.74.0", "releaseDate": "...", "url": "...", "notes": "..."},
  "edge":   {"version": "2.75.0", "releaseDate": "...", "url": "...", "notes": "..."},
  "updateAvailable": true,
  "channel": "stable"
}
```

- Proxies `api.github.com/repos/ttlequals0/MinusPod/releases` through
  the existing safe_http utilities; result cached 6 hours in-process.
- `?refresh=true` bypasses the cache (used by the manual button) with
  its own rate limit so the button cannot hammer GitHub. The 60/hr
  unauthenticated GitHub limit is ample at this cadence.
- Stable = newest non-prerelease; edge = newest release of any kind.
- `updateAvailable` is true only when the selected channel's version
  parses strictly newer than the running version (semver-style
  comparison of the existing X.Y.Z scheme).
- The running version's release date is found by locating the running
  version in the same releases feed; if absent, the field is omitted.
- New persisted settings: `update_check_enabled` (default on),
  `update_channel` (`stable` | `edge`, default `stable`).
- Daily background check reuses the existing background-loop pattern.
  On first sighting of a version newer than both the running version
  and a persisted `last_notified_version`, it fires notifications once
  and advances the marker.

## 5. Notifications

- `webhook_service.py`: add `EVENT_UPDATE_AVAILABLE = 'Update
  Available'` to VALID_EVENTS with a payload of channel, version,
  release date, and release URL.
- `email_service.py`: matching formatter, subject
  `[MinusPod] Update Available: X.Y.Z`, body rows for channel,
  version, date, and a release-notes link.
- Fired only by the daily background check, once per version. Manual
  checks never notify.

## 6. Frontend

- Settings > System Status: release date next to the version, channel
  dropdown (Stable / Edge), auto-check toggle, "Check for updates"
  button calling `?refresh=true` and showing the result inline, and a
  Changelog link to the GitHub releases page.
- Banner: when `updateAvailable`, a dismissible banner under the top
  nav ("MinusPod X.Y.Z is available" with a link). Dismissal is stored
  per-version in localStorage so the banner returns only for the next
  release.

## 7. Testing

- Backend unit tests: updates endpoint with mocked GitHub responses
  (cache behavior, refresh path, prerelease filtering, version
  comparison, missing-self date), notify-once logic, new webhook/email
  event formatting. Existing tests/app_bootstrap.py pattern.
- Frontend tests: banner render/dismiss, settings controls, check
  button states. Existing test patterns in frontend suite.
- Lint and tsc gates as usual.

## 8. Rollout and scope split

Two sub-projects, delivered separately:

1. Process and tooling (no app code): `scripts/publish_release.sh`,
   `scripts/promote_release.sh`, the release-notes skill, CHANGELOG
   Breaking convention, README/docs section on channels (stable vs
   latest, which to pick, compose examples with `:stable` /
   `:stable-cpu`), first tag, and first stable promotion.
2. App feature (ships as a normal 2.73.x release): updates endpoint,
   settings, background check, notifications, frontend UX. Closes
   issue #567.

Default channel is `stable` by design: it is the recommendation the
formal process exists to make, even though current users effectively
run edge.
