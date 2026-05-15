# MinusPod community patterns

This directory holds the crowdsourced ad pattern set. Each file is one pattern. Other MinusPod instances pull the manifest from this directory on a schedule (opt-in) and import patterns so a fresh install benefits from coverage built up elsewhere.

-----

## Directory structure

```
patterns/
├── README.md                  ← this file
├── CONTRIBUTING.md            ← submitter-facing PR explainer
├── community/
│   ├── index.json             ← manifest published to clients
│   ├── <sponsor>-<uuid>.json  ← one file per pattern
│   └── ...
└── vocabulary.json            ← reference copy of the canonical tag list (canonical version lives in the app code)
```

-----

## How sync works

Opted-in MinusPod instances fetch:

```
https://raw.githubusercontent.com/ttlequals0/MinusPod/main/patterns/community/index.json
```

on a configurable cron schedule (default weekly). The manifest lists every published pattern with its `community_id` and `version`. The client:

- Inserts patterns it has not seen before
- Updates patterns whose `version` is higher than the local copy
- Deletes patterns that are no longer in the manifest

Patterns the user has edited locally or pinned with **Protect from sync** are skipped on update and delete.

Failed or partial fetches make zero changes. Deletion only happens on a fully successful pull.

-----

## Manifest format

```json
{
  "manifest_version": 1,
  "published_at": "2026-05-14T00:00:00Z",
  "vocabulary_version": 1,
  "patterns": [
    {
      "community_id": "uuid-string",
      "version": 1,
      "data": { ... full pattern JSON inline ... }
    }
  ]
}
```

Patterns are embedded inline so the client fetches everything in a single request. The individual files in `community/` are the source of truth for review and curation; the manifest is regenerated from them on publish.

`vocabulary_version` lets clients detect when the tag vocabulary has changed and refresh accordingly.

-----

## Pattern file format

```json
{
  "community_id": "uuid-string",
  "version": 1,
  "submitted_at": "2026-05-14T00:00:00Z",
  "submitted_app_version": "2.3.1",
  "sponsor": {
    "name": "NordVPN",
    "aliases": ["Nord VPN"],
    "tags": ["vpn", "tech", "security", "universal"]
  },
  "sponsor_match": "exact",
  "scope": "global",
  "text_template": "...",
  "intro_variants": ["...", "..."],
  "outro_variants": ["...", "..."],
  "matching_params": {
    "confidence_threshold": 0.85,
    "min_text_length": 50
  }
}
```

Fields:

- `community_id` - stable identifier across all instances
- `version` - increments when the pattern is updated upstream
- `submitted_at` - ISO 8601 timestamp of the original submission
- `submitted_app_version` - version of the submitting MinusPod app, used for triage
- `sponsor.name` / `sponsor.aliases` - sponsor identity, looked up against the seed list on import
- `sponsor.tags` - multi-tag classification, all values must exist in the vocabulary
- `sponsor_match` - set by the app on submission: `exact`, `alias`, `fuzzy`, or `unknown`
- `scope` - always `global` for community patterns
- `text_template` / `intro_variants` / `outro_variants` - the actual matched text
- `matching_params` - pattern-level matching configuration

-----

## Tag vocabulary

49 tags in a flat namespace. The canonical source is `src/seed_data/tag_vocabulary.csv` in the MinusPod app code (read by `src/utils/community_tags.py`). A reference copy lives in `vocabulary.json` alongside the patterns for human readability.

Tag categories (informal grouping for documentation only; they are all in one namespace):

- **Podcast genres** (26): `arts`, `books`, `business`, `comedy`, `education`, `language_learning`, `self_improvement`, `fiction`, `history`, `health`, `mental_health`, `kids_family`, `leisure`, `gaming`, `automotive`, `music`, `news`, `politics`, `religion`, `science`, `society_culture`, `travel`, `sports`, `technology`, `true_crime`, `tv_film`
- **Sponsor industries** (22): `tech`, `saas`, `vpn`, `security`, `finance`, `insurance`, `food`, `meal_kit`, `beverage`, `supplements`, `apparel`, `home_goods`, `mattress`, `home_security`, `personal_care`, `auto`, `telecom`, `jobs`, `streaming`, `gambling`, `nicotine`, `dtc`
- **Special** (1): `universal` (sponsor advertises across all podcast genres)

### Matching rule

A community pattern is eligible for a podcast when ANY of the following is true:

1. The sponsor has the `universal` tag
1. The sponsor's tag set and the podcast's tag set share at least one tag
1. The sponsor has no tags (fallback)
1. The podcast has no tags (fallback)

This filter runs before any fuzzy text matching. Patterns filtered out by tags never enter the matching loop, which is how the system stays fast as the community set grows.

### Podcast tagging

Podcasts carry their own tag set from the same vocabulary. The effective tag set is the union of three sources. The RSS `<itunes:category>` is parsed when the podcast is added, mapped to vocabulary tags via a fixed table (e.g. `Technology` → `technology`, `True Crime` → `true_crime`). Episode-level RSS category tags add to the effective set for that episode. User-added tags entered through the UI are useful when the RSS metadata is missing or wrong, or when a specific episode departs from the podcast's normal genre.

User-added tags never override or replace RSS-derived tags; they only add to them. A podcast with no tags from any source falls back to matching all community patterns (matching rule 4).

API:

- `GET /api/podcasts/{id}/tags` - returns the effective tag union with source breakdown
- `PUT /api/podcasts/{id}/tags` - updates the user-added tag layer (the only mutable layer)

-----

## How to submit a pattern

The recommended path is to open the Export dialog on the Patterns page in MinusPod and pick the **Submit to community** destination. The app generates the JSON, strips PII, runs quality gates, and opens a prefilled GitHub PR per selected pattern.

See `CONTRIBUTING.md` for the full explainer on what gets submitted, what gets stripped, and what the automated checks look for.

Manual submission (without the app) is possible but discouraged. You would need to:

1. Hand-craft a pattern JSON file matching the format above
1. Open a PR adding the file to `patterns/community/`
1. The GitHub Action will validate; expect the same checks as automatic submission

-----

## How to add a sponsor to the seed list

The authoritative seed list lives in `src/seed_data/sponsors_final.csv` and is loaded into `known_sponsors` by the v2.4.0 schema migration. Adding a sponsor is a PR to that CSV; the next instance startup picks it up via the reseed migration step.

When a community pattern is submitted with an unknown sponsor, the GitHub Action flags it. A maintainer then either:

- Opens a follow-up PR adding the sponsor to the seed list, then merges the original pattern PR
- Decides the submitted sponsor is actually an alias of an existing one and updates the alias list
- Closes the PR if the sponsor is not appropriate for community sharing

-----

## Reviewer workflow

Maintainer responsibilities for incoming PRs:

1. Check the Action result on the PR. Red checks must be resolved before review proceeds.
1. Read the Action's comment for context (variant suggestions, sponsor flags, dedupe notes).
1. Verify the pattern looks reasonable: real ad copy, no obvious junk, sponsor identification correct.
1. If the Action flagged a variant suggestion and the suggestion is correct, manually edit the existing pattern file in the PR to add the new text to its `intro_variants` or `outro_variants` array, then close the new-file PR. (Auto-apply is deferred to a later release.)
1. If a sponsor is flagged as unknown, decide whether to add it to the seed list (separate PR) or treat it as an alias.
1. Approve and merge when satisfied.

-----

## Operational notes

### Publishing a new manifest

The manifest at `community/index.json` is regenerated automatically on every push to `main` that touches `patterns/community/`. The `regenerate-manifest` GitHub Action scans `patterns/community/*.json`, bumps `published_at`, embeds all patterns inline, and commits the updated `index.json` back to `main`.

Maintainers do not need to run anything manually. Merge the pattern PR and the manifest updates within a minute.

For local testing or recovery from a workflow failure, the same logic can be invoked manually:

```
python -m src.tools.generate_manifest
```

This rewrites `index.json` in place. Commit it like any other file.

### Vocabulary changes

When new tags are added or removed from the canonical vocabulary:

1. Update `src/seed_data/tag_vocabulary.csv` in app code (requires app release).
1. Update `vocabulary.json` in this directory to match.
1. Bump `vocabulary_version` in the manifest (handled automatically by the regeneration workflow when the source files change).
1. Clients refresh their reference on the next sync.

### Removing a bad pattern

Open a PR removing the JSON file from `patterns/community/`. On merge, the regeneration workflow rebuilds the manifest without the removed pattern. On the next client sync, instances delete the pattern (unless the user has pinned it locally).

-----

## Privacy

No personal information is captured in any pattern file. See `CONTRIBUTING.md` for the full list of what gets stripped before submission.

The GitHub PR author identity is visible because PRs are public. This is a property of GitHub, not MinusPod. Submitters who want anonymity at that level should use a separate GitHub account.

-----

## Questions

Open an issue on the main MinusPod repo for anything not covered here.