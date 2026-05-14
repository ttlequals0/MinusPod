# Community ad patterns

This directory holds ad patterns that MinusPod instances can pull in
automatically so a new install gets coverage on common sponsors without
having to build its own library first.

## How it works

- One JSON file per pattern in `examples/`. Filename is
  `<sponsor-slug>-<short-uuid>.json`.
- `index.json` lists every pattern and ships in the manifest the server
  fetches from
  `https://raw.githubusercontent.com/ttlequals0/MinusPod/main/patterns/community/index.json`.
- Pull requests touching this directory run the
  `Community Pattern Validation` workflow, which checks the file against
  the same gates the in-app export uses (length, sponsor presence, tag
  vocabulary) and runs a three-tier dedupe pass against existing
  patterns.
- A green check from the validator means the file is well-formed and not
  a duplicate. It does NOT mean the content is correct — that's what
  human review is for.

## Submitting a pattern

The easiest path is the **Submit to community** button in the patterns
page of any MinusPod install. It runs the export pipeline locally, strips
PII, classifies the sponsor against the seed list, and opens a prefilled
GitHub PR.

If you'd rather author one by hand, the schema is:

```jsonc
{
  "community_id": "uuid4",
  "version": 1,
  "scope": "global",
  "sponsor": "Squarespace",
  "sponsor_aliases": ["Square Space"],
  "sponsor_tags": ["tech", "saas", "universal"],
  "text_template": "...",
  "intro_variants": ["..."],
  "outro_variants": ["..."],
  "avg_duration": 30.0,
  "submitted_at": "2026-05-14T00:00:00Z",
  "submitted_app_version": "2.4.0"
}
```

All tags must come from `src/seed_data/tag_vocabulary.csv` (plus the
special `universal` tag). The pattern's sponsor must appear in
`src/seed_data/sponsors_final.csv`; new sponsors are accepted but get
flagged for human review.

## What gets rejected

- Missing required fields, malformed JSON, or invalid tags.
- Text shorter than 50 chars or longer than 3500 chars.
- Pattern duration over 120 seconds.
- Sponsor name (or alias) absent from `text_template`.
- Another sponsor's name appearing in `text_template`.
- Duplicates of an existing pattern (95%+ canonical-text similarity).

Variant patterns (75-95% similarity) are flagged but not rejected; the
PR comment suggests merging the new text into the existing pattern's
`intro_variants` / `outro_variants` array.
