# Audio fingerprint matching, review and settings: design

Ships with the "Running without an LLM" work
([2026-11-10](2026-11-10-llm-optional-design.md)).

## Context

MinusPod stores a Chromaprint fingerprint for some ad patterns and matches it
against every new episode as the first detection stage. An operator cannot see
any of it. No API route or screen lists a fingerprint, says whether a pattern
has one, or shows what it matched. The only control is disabling or deleting
the whole pattern, which also stops its text matching.

The matcher has limits of its own:

- It compares the first 10 seconds of a stored ad at 2 second steps. An ad that
  starts between steps scores lower, and the end of the match is assumed from
  the stored length, not measured.
- It ignores pattern scope. Every active fingerprint is tried against every
  episode on the install.
- A fingerprint is created only when an LLM or SponsorBlock detection creates a
  pattern, and is never rebuilt when the ad's edges are corrected.
- Its thresholds are source constants with no setting.

With `none` as the LLM provider, fingerprints and text patterns are the main
detectors on a regular feed, so these limits matter more.

## Goal

An operator can see which patterns have audio, hear it, see what it matched and
how well, correct its edges, turn it off or remove it, and tune matching from
Settings. The matcher measures the whole ad, so the reported position and
strength are real.

## Non-goals

- Finding repeated audio across episodes with no known ad, the way Intro
  Skipper does. Matching still needs a stored fingerprint.
- Storing audio clips. Playback and rebuilds read a retained original.
- Keeping an original past its retention period because a fingerprint points
  at it.
- A new "Needs review" band for partial matches. A match cuts or is ignored,
  as today.
- A dry run for settings changes.
- A published benchmark.
- Sharing fingerprints through community patterns or pattern export.
- The cue template scans (recurrence, cross-episode, dismissals). They use
  Chromaprint but are a separate system and do not change.

## Decision: matcher

The fixed-step scan in `src/audio_fingerprinter.py` is replaced and
removed. There is one matcher and no switch between two.

For each stored fingerprint in scope, the matcher finds candidate alignments
between the fingerprint and the episode fingerprint by offset, then walks each
candidate and measures the run of matching points at that one alignment. A gap
inside the run is bridged up to the gap tolerance. Where agreement stops for
longer than the gap tolerance, the run is split and each piece must pass every
gate on its own. An ad with a long damaged stretch is found as shorter
matches or missed. The best run per position wins. When the whole-file
fingerprint cannot be produced, the episode gets no audio matches, a warning
names it, and the rest of detection runs as usual.

A match reports:

| Field | Meaning |
|---|---|
| `start`, `end` | Measured edges of the run in original-audio time |
| `similarity` | Mean bit similarity over the run, 0 to 1 |
| `coverage` | Matching points divided by the stored fingerprint's points, 0 to 1. A point inside a stretch of identical values (silence, a tone) carries no information and never counts as matching |

A run is a match when `similarity` is at least the strictness setting,
`coverage` is at least the minimum coverage setting, and the run also passes
two evidence gates. Unrelated real audio agrees closely for a few seconds, so
a match needs a minimum amount of novelty-weighted agreeing audio (or 90 percent
of what a shorter pattern can supply) and a minimum amount of very close
agreement. Anything else is ignored and not recorded.

A fingerprint with too little distinctive audio, or whose share of
non-constant points is below the minimum coverage, could never pass these
gates. It is refused when stored (reason `not_distinctive`) instead of sitting
unmatched. If the minimum coverage setting is raised later, a stored
fingerprint that can no longer match shows `matchable: false` in its detail.

The marker for a match spans the measured `start` to `end`. Today the end is
the start plus the stored length. `fingerprint_match_start` and
`fingerprint_match_end` carry the same measured edges, so the code that treats
only the first 10 seconds as measured (`utils/markers.py`, the reviewer prompt
and its edge check) now sees the whole span as measured.

Everything after the match is unchanged: stage label `fingerprint`, stage
priority, the false-positive correction skip, the validator, the reviewer and
its hold on a rejected fingerprint, the short-cut exemption and learning rules.

The whole-file fingerprint timeout scales with episode duration and is never
below today's 120 seconds. The 600 second scan limit stays.

How candidates are indexed, batch sizes and other internals are left to the
builder. The requirement is that a 3 hour episode against the fingerprints in
scope is not slower than today, checked by the comparison run below.

## Decision: scope

Audio follows its pattern's scope, with the same rule the text matcher uses: a
`podcast` pattern matches only that podcast, a `network` pattern only podcasts
on that network, and a `global` pattern everywhere. Both matchers
call one shared scope rule (`pattern_passes_scope` in
`src/utils/pattern_scope.py`), including community-pattern tag eligibility, so
the two cannot drift. The podcast and the episode's effective network (the
feed's network override, else its detected network) are passed down to both.
Before this release the detector never passed a network to the text matcher, so
a `network` pattern never matched; that is fixed here for text and audio alike.
Changing a pattern's scope to `network` records the network it applies to.

An existing install may have fingerprints that have been matching outside their
scope. A one-shot migration reads stored episode markers with stage
`fingerprint`, records them as match history (below) and flags the ones that
fall outside the pattern's scope. The pattern's audio section then shows how
many past matches were out of scope, so the operator can widen the pattern
deliberately. The migration logs the affected pattern ids once. It deletes
nothing.

The backfill is partial. It covers only markers still stored on
episodes and skips markers whose pattern no longer exists. A row takes
similarity and coverage from the stored marker when it recorded them and
otherwise stores them empty, shown as "-" and left out of means. An old
row's span is the marker's own span, since the old matcher did not measure
one. A
held marker that was later rejected is written as a rejected row when a
matching reject correction exists, so "Not this ad" history is kept. The rows
and the migration mark are written in one transaction; a failed run writes
nothing and retries on the next start. The log names affected pattern ids, up to
a cap, with a count of the rest.

## Decision: when a fingerprint is created

A fingerprint is created or rebuilt when a person or a trusted detector sets an
ad's edges on an episode whose original is retained.

| Source | Result |
|---|---|
| LLM or SponsorBlock detection that creates a pattern | Created, as today |
| Manual "Add ad" | Created (defined in the 2026-11-10 spec) |
| A confirm correction | Created from the confirmed span if the pattern it creates or links to has no audio |
| An adjust correction | Rebuilt from the new span |
| Text-pattern match | Only by the manual action "Add audio to pattern" on that detection, using the detection's span as shown. To use other edges, adjust the ad first |
| Imported or community pattern | None. It gains audio through the rows above |

A manual "Add ad" or a create correction whose text splits into several
sponsors fingerprints each pattern over its own span only. A pattern whose own
range cannot be found in the transcript gets none (`span_covers_multiple_patterns`).
A pattern that already has a fingerprint keeps it (`fingerprint_exists`); confirm
and Add ad never replace one, adjust and "Add audio to pattern" do. An adjust
rebuilds only the adjusted marker's pattern.

Creation is best effort everywhere. The pattern or correction is saved either
way, and the route reports `fingerprintStored` and `fingerprintSkippedReason`
(`audio_removed`, `fingerprint_exists`, `span_covers_multiple_patterns`,
`original_not_retained`, `segment_too_short`, `not_distinctive`,
`fingerprint_failed`, reported in that order of precedence) as the 2026-11-10 spec defines for "Add ad". Confirm and
adjust go through the existing corrections route and its `correctionType`.
`AudioFingerprinter.store_fingerprint` returns only a boolean today, so
it changes to report which reason applied.

After "Delete audio" (below), the automatic rows do not create a fingerprint
for that pattern again. Adjusting edges or "Add audio to pattern" are explicit
and do.

## Decision: source audio

A fingerprint cannot be played. Preview, edge editing and rebuilds need the
audio it came from, and they read a retained original. No clip is stored.

Each fingerprint records the episode and span it was built from. Each match
records the episode and measured span. The source for preview or a rebuild is
the newest of those whose original still exists: the latest match first, then
the episode it was built from.

When none is left, preview, edge editing and rebuild are unavailable with the
reason `original_not_retained`, the same reason recut and "Add ad" use. The
fingerprint keeps matching. It becomes playable again the next time it matches
an episode. Originals expire on the existing retention settings; nothing here
changes them.

## Decision: controls

Audio is controlled on its own, inside the pattern it belongs to.

| Action | Effect |
|---|---|
| Turn audio off or on | The fingerprint stops or resumes matching. Text matching is not affected. Disabling the pattern still silences both |
| Delete audio | The fingerprint and its match history are removed. The pattern stays |
| Edit edges | Move the start or end on the source audio, listen, save. The fingerprint is rebuilt from the new span. The source episode's own markers and cuts are not touched |
| Not this ad | On one match. This is the existing reject correction for that episode, sent with the match's measured span. The match row is marked rejected when the correction is saved |

A reject keeps everything it does today, including the pattern's
false-positive count and whatever already reads that count. Nothing new
disables audio automatically.

The on/off flag and the source episode and span are stored with the
fingerprint. The "audio was deleted" marker is stored on the pattern, since the
fingerprint row is gone by then.

Existing pattern operations keep their behavior and carry the new data with
them:

| Operation | Audio |
|---|---|
| Merge | The keeper keeps its audio, history and deleted marker. Folded fingerprints and their history are deleted, as fingerprints are today |
| Dedupe | A fingerprint moved to the keeper takes its history, flag and source with it |
| Split | Unchanged from today |
| Delete pattern, delete feed | Fingerprint and history go with the pattern |

## Decision: match history

Each match is stored: pattern, episode, measured start and end, similarity,
coverage, whether it was in scope, whether any other detector flagged the same
span, and when. A match is stored when its ad is in the episode's final markers
as cut, held for review or kept. A marker the validator rejects, or one left
uncut below the confidence gate, is not a match the pipeline kept and is not
stored. Reprocessing an episode replaces that episode's rows, except rows
marked "Not this ad", which stay.
A rejected row does not count in a pattern's match count, mean similarity or
last-matched time. Rows go
when the pattern or its audio is deleted, and when its episode row is
deleted, so counts and the last match reflect only existing episodes. An
episode whose audio merely expired keeps its rows; they have no playable
source.

"Found by audio only" means no other detection stage contributed to the merged
marker for that span. It is worked out when the match is written.

## Decision: API

All of this is reachable through `/api/v1` before the UI uses it. Names follow
the existing camelCase payloads.

| Route | Purpose |
|---|---|
| `GET /patterns`, `GET /patterns/{id}` | Each pattern gains an `audio` summary: state (`active`, `off`, `none`), whether a source is available, match count and mean similarity |
| `GET /patterns/{id}/audio` | Detail: state, whether it is matchable under the current settings, length, created time, the span it was built from, current source and whether it is available, for a pattern with no audio the span of its own marker that audio could be built from (`addSource`), counts (matches, rejected, out of scope), mean similarity, last matched |
| `PUT /patterns/{id}/audio` | Turn audio on or off |
| `DELETE /patterns/{id}/audio` | Delete audio |
| `POST /patterns/{id}/audio/rebuild` | Rebuild from an episode and span. Used by edge editing and "Add audio to pattern" |
| `GET /patterns/{id}/audio/matches` | Match history, newest first, paginated, 10 per page by default |
| `GET /patterns/{id}/audio/preview` | The source span as audio, for the source or a given match |
| `PUT /settings/ad-detection`, `GET /settings` | The settings below, with defaults and reset |
| `GET /stats/audio-matching` | The four numbers below for a period |

"Not this ad" uses the existing corrections route. The builder picks exact
paths and shapes to fit the neighboring routes and documents every route in
`openapi.yaml` and `docs/api-and-webhooks.md`. Preview serves original-audio
time through the same original lookup the cue template routes use
(`_resolve_original_audio`); it is a new route unless an existing one already
serves a span of an original. The pattern list computes its audio summary in
one aggregate query for the whole list. The Stats route takes the same
period parameter as the other Stats routes, and "Not this ad" is counted from
the corrections table by the time the correction was made.

## Decision: settings

The Settings page gains an "Audio fingerprinting" section. It is saved with
the page like the audio cue section, stored in the settings registry with
defaults and reset, and included in config export and import.

| Setting | Meaning | Starting value |
|---|---|---|
| Audio fingerprinting | On or off for the install | On |
| Match strictness | Minimum similarity for a match | 65% (today's threshold); allowed 65% to 95% |
| Minimum coverage | Share of the stored ad that must be found | 50%; allowed 10% to 100% |
| Gap tolerance | Longest dropout bridged inside a match | 3.5 s; allowed 0 to 15 s |
| Minimum ad length | Shortest span that gets a fingerprint | 5 s (today's value); allowed 3 to 30 s |

Strictness cannot go below 65%: below that the setting had no measured effect
in the matcher.

Starting values are placeholders. The comparison run sets the shipped defaults,
and `docs/` records each with one line of reasoning. Changing a setting affects
later runs and "Re-detect ads"; it never re-fingerprints stored ads.

The section stays enabled when the LLM provider is `none`.

## Decision: UI

Follows `design-guide/UI-CONTRACT.md`.

- Patterns table: an "Audio" column. It shows the mean match as "91% match",
  "Audio off", "No source audio", or "-" when the pattern has none.
- Pattern dialog: one scrolling dialog, as today, with an "Audio" section under
  the text. It holds the state and on/off control, a play control, the stats
  (matches, mean match, "Not this ad" count, last matched), the out-of-scope
  note when it applies, "Edit edges" and "Delete audio". The footer stays
  pinned.
- Recent matches: under the Audio section. Episode, position, match, covered
  ("38s of 45s"), outcome, with "Play" and "Not this ad" per row. Paginated at
  10. A row with no retained original says "Source audio expired" and has no
  actions.
- Edit edges opens the existing ad editor on the source episode with the
  fingerprint's span as its one region. Saving calls the rebuild route only
  and sends no correction. The editor works on an episode's detected ads
  today, so it needs a mode that takes a single span and a save handler.
  Playback uses the same original-audio playback the cue detections section
  uses, behind the existing audition button. This adds no second player or
  waveform.
- A pattern with no audio shows "No audio fingerprint." and, when its source
  episode still has an original, an action to add one.
- Detected ads and the episode page: a text-pattern detection whose pattern has
  no audio gains "Add audio to pattern".
- Stats page: an "Audio matching" section that follows the page's period
  filter, with four tiles: patterns with audio (of total), audio matches,
  found by audio only, and marked "Not this ad".
- Settings: the section above.

## Migration

Additive only. Existing fingerprints are kept and used by the new matcher as
they are. New columns go through `_add_column_if_missing`; the history table
and the marker backfill are gated by `schema_migrations`. A fingerprint with no
recorded source span uses its pattern's origin episode when the pattern's span
can be recovered from stored markers, and otherwise has no source until it
next matches.

## Validation

Before release:

- A one-off comparison script runs the old and new matchers over episodes with
  retained originals on a real instance and lists matches gained, lost and
  moved, the ones lost to scope, and wall time for both. Its output sets the
  defaults and is not committed.
- `tests/prod_replay` is run, as far as its fixtures cover fingerprint matches.

The release does not go ahead if the new matcher loses matches the old one
made in scope, unless each is explained.

## Tests

- Matcher: planted fingerprints at known offsets, including off-step starts,
  truncated ads, a gap inside and beyond the tolerance, two ads back to back,
  and a pattern longer than the remaining audio. Measured start, end,
  similarity and coverage are asserted.
- Scope: podcast, network and global patterns against in-scope and
  out-of-scope episodes.
- Creation: each source in the table, each skip reason, and no automatic
  re-creation after "Delete audio".
- Source resolution: latest match, then origin, then `original_not_retained`.
- Routes: every route above, including pagination, and OpenAPI conformance and
  registry tests.
- Migration: an existing database keeps every fingerprint, gains history from
  stored markers and flags out-of-scope rows. A second start changes nothing.
- Settings: validation, defaults, reset, config export and import.
- Frontend: vitest for the column, the dialog section, each empty and expired
  state and the Stats section, plus Playwright screenshots at the UI-CONTRACT
  widths.

## Docs

`openapi.yaml`, `docs/api-and-webhooks.md`, `docs/how-it-works.md`,
`docs/glossary.md`, `docs/workflows.md`, `docs/llm-providers.md` (the section
on running without an LLM) and `CHANGELOG.md`.

## Alternatives considered

- Storing a short clip with each fingerprint. Playback would always work, but
  it adds a second copy of audio to manage and a rule that differs from recut
  and "Add ad".
- Keeping originals that a fingerprint points at. Most episodes that produce a
  pattern would be kept, which turns original retention off in practice.
- Running the new matcher beside the old one for a release. That is two
  matchers in the pipeline and a second release to switch.
- Holding partial matches for review. It changes what the review queue
  contains.
