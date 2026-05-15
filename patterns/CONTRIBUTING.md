# Contributing community patterns

This explains what happens when you submit a pattern, what gets stripped before submission, and what the automated checks look for.

-----

## What is a community pattern

A community pattern is an ad pattern from your local MinusPod instance that you've chosen to share. Once accepted, it ships to other MinusPod users via the periodic sync. You retain everything you had locally. Submission is a copy, not a move.

-----

## What gets submitted

When you pick **Submit to community** in the Export dialog, the app runs quality gates over your selection, lets you preview the ready vs rejected split (with reasons), and downloads one bundle file. The bundle has this shape:

```json
{
  "format": "minuspod-community-submission",
  "bundle_version": 1,
  "submitted_at": "...",
  "submitted_app_version": "2.4.5",
  "pattern_count": N,
  "patterns": [ /* one entry per pattern that passed quality gates */ ]
}
```

Each entry in `patterns[]` includes:

- Pattern text (`text_template`, `intro_variants`, `outro_variants`)
- Sponsor name and aliases
- Tags
- Pattern scope and matching parameters
- A fresh UUID (`community_id`)
- Version number (starts at 1)
- Submission timestamp
- App version that submitted the pattern

The bundle does not include any data identifying you, your podcasts, or your listening habits. The PR-side validator handles bundle files natively (one validation per entry), and the manifest builder flattens them into per-pattern entries in `patterns/community/index.json`, so the maintainer does not have to split them on merge.

-----

## What gets stripped before submission

### Local identifiers

The following fields are removed entirely:

- Local pattern ID
- Podcast ID and network ID
- DAI platform
- Created/updated timestamps
- Match counts and last match timestamp
- Confirmation count and false positive count
- Local reviewer notes
- The `protected_from_sync` flag
- The `source` field (set fresh by the importer)

These reveal which podcasts you listen to.

### PII in the pattern text

Pattern text is scanned and the following are stripped:

**Email addresses with consumer domains.** Emails at these domains are removed: `gmail.com`, `yahoo.com`, `aol.com`, `hotmail.com`, `outlook.com`, `icloud.com`, `me.com`, `mac.com`, `protonmail.com`, `proton.me`, `mail.com`, `gmx.com`, `gmx.net`, `yandex.com`, `yandex.ru`, `qq.com`, `163.com`, `live.com`, `msn.com`, `hey.com`, `fastmail.com`, `tutanota.com`. Business emails like `support@nordvpn.com` are kept because they are part of the sponsor's actual ad copy.

**Phone numbers that are not toll-free.** Toll-free numbers are kept (US/CA `800`, `833`, `844`, `855`, `866`, `877`, `888`; UK `0800`, `0808`; AU `1800`; international `+800`). Anything else matching a phone pattern is removed.

The PII strip list is best-effort and tunable. Open an issue if you find a gap.

-----

## Quality checks before submission

The app refuses to submit if any of these are true:

- Pattern text is shorter than 50 characters
- Pattern text is longer than 3500 characters
- Pattern duration is longer than 120 seconds
- You have not confirmed the pattern at least once locally
- Your false positive count exceeds your confirmation count
- The pattern is not tied to a single sponsor
- The assigned sponsor's name (or any known alias) does not appear in the pattern text
- A different sponsor's name appears in the pattern text (multi-sponsor contamination)
- Any tag on the pattern is not in the canonical vocabulary

When a check fails, the app shows which one and does not generate a submission.

-----

## What happens after you click submit

1. The app runs the quality gates and shows a preview: how many patterns will pass, and the reasons for any rejections.
1. You confirm; `minuspod-submission-<id>.json` downloads to your machine.
1. You fork `ttlequals0/MinusPod`, drop the file into `patterns/community/` on a new branch, commit, push, and open a PR. A CLI snippet is shown right after the download.
1. The GitHub Action validates the bundle (one validation per entry).
1. A maintainer reviews the PR.
1. If accepted, every pattern in the bundle joins the next published manifest and reaches other instances on their next sync.

You need git installed (or the `gh` CLI) for the last step. The app does not push anything on your behalf.

-----

## What the automated PR checks do

The GitHub Action runs the same quality checks as the in-app submission as a safety net, plus dedupe.

### Re-validation

All quality checks run again. If something was missed (or someone hand-edited the JSON), the action catches it.

### Single-pattern check

Each submitted file must describe exactly one ad. The action scans `text_template` for the names (and aliases) of any other seed sponsor; if any match on a word boundary, the PR is rejected with the list of foreign sponsors found. Usually that means a multi-sponsor ad block got pasted in. Trim the text to one sponsor and resubmit. Reviewers should eyeball this too: automation catches obvious stitches but not edge cases like a sponsor that isn't in the seed list yet.

### Tag validation

Every tag is checked against the canonical vocabulary. Unknown tags fail the check.

### Sponsor validation

The sponsor name is looked up in the seed list. An exact or alias match passes silently. A fuzzy match passes but the action flags it in a comment for the reviewer to confirm. An unknown sponsor also passes (a new sponsor isn't a rejection) but the action flags it in a comment for maintainer triage.

### Dedupe

The action canonicalizes the new pattern's text (lowercase, strip punctuation, remove stopwords, dates, day names) and compares it against every existing community pattern for the same sponsor:

- **95% or higher similarity** → DUPLICATE. PR rejected. Comment points to the existing pattern.
- **75% to 94% similarity** → VARIANT. PR passes. Comment suggests merging the new text into the existing pattern's variants list. The maintainer decides during review.
- **Less than 75% similarity** → DISTINCT. Accepted as a new pattern.

Genuinely different ads from the same sponsor are expected. NordVPN has had many ad scripts; each one is a distinct pattern.

-----

## What the maintainer does

The maintainer reviews the PR and either:

- Merges as-is
- Asks for revisions
- Merges after manually applying a variant merge
- Closes as duplicate
- Triages a flagged sponsor before merging

Approved patterns land in the next published manifest and reach opted-in instances on their next sync.

This process is new and not set in stone. Open an issue if you have ideas to improve it.

-----

## What does NOT get shared

- Your username, email, IP, or any account identifier (you do not have one)
- Names of podcasts you listen to
- Episode information
- Any metric about your listening habits
- Any local config from your MinusPod instance
- Anything that could deanonymize you across submissions

The only personally-attributable trace is the GitHub PR itself, opened from your GitHub account. For anonymity at the GitHub level, use an account that is not tied to your identity. MinusPod does not handle this.

-----

## Questions or issues

Open an issue on the main MinusPod repo if:

- A PII pattern is being missed by the strip rules
- A tag in the vocabulary needs a change
- A sponsor needs to be added to the seed list
- The PR validator gives an unclear error
- A pattern was incorrectly rejected as a duplicate

This document evolves with the project.