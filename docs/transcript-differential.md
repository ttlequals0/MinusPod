# Upstream Transcript Differential

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [What it is](#what-it-is)
- [Which episodes qualify](#which-episodes-qualify)
- [How a gap is found](#how-a-gap-is-found)
- [How a gap is used](#how-a-gap-is-used)
- [Settings](#settings)
- [Limits](#limits)

## What it is

Some publishers ship a `podcast:transcript` tag on their RSS items whose
transcript was produced before the ad reads were spliced in, so it is already
ad-free. The upstream transcript differential diffs MinusPod's own Whisper
transcript of the downloaded audio against that publisher transcript: any
block of speech in the Whisper transcript that the publisher's copy omits is
a candidate ad.

This is an additional evidence signal on top of the detector, not a
replacement for it. It never cuts on its own: a gap it finds either
corroborates a detection another stage already made, or becomes a new
held-for-review marker for a human to confirm. It is best effort, so a fetch
or parsing failure never fails the run; the episode processes as if the stage
had not run.

## Which episodes qualify

- The feed must carry a `podcast:transcript` tag MinusPod can read: `text/vtt`,
  `application/srt`, `application/x-subrip`, `application/json` (podcast
  namespace transcript JSON), `text/plain`, or `text/html`. When an item lists
  more than one, MinusPod keeps the best type in that order.
- Local feeds have no publisher transcript to compare against and always skip
  the stage.
- The per-feed setting overrides the global default. Inherit follows the
  global setting; On enables the stage for that feed even when the default is off.
- The publisher's transcript URL is read-only evidence. MinusPod never serves
  it to subscribers; see [Podcasting 2.0 > Regenerated for the processed
  audio](podcasting-2.0.md#regenerated-for-the-processed-audio).

## How a gap is found

Both transcripts are tokenized and aligned word by word. Runs of Whisper-only
words, at least 40 words and at least 10 seconds of audio, become a gap span;
gaps within 3 seconds of each other merge into one. Below 60 percent word
coverage between the two transcripts, the comparison is treated as unreliable
(wrong episode, a heavily re-edited transcript, or a different language) and
produces no spans at all.

When the publisher's transcript carries timestamps, MinusPod also checks
whether its time axis jumps across the gap by roughly the gap's own length,
the signature of a block that was physically removed rather than just
summarized differently. A gap with that signature is marked
`offset_confirmed` and carries a higher confidence (see below); a gap without
it, or found in an untimed transcript, still counts as evidence but at the
default confidence.

The alignment only ever reads text; it never changes what gets cut by itself.

## How a gap is used

Each gap becomes a held marker: category `sponsor`, confidence 0.6 (0.75 when
`offset_confirmed`), reason "Upstream transcript omits this span", and hold
reason `transcript_differential_unreviewed`. It never auto-cuts.

- If a fingerprint, text pattern, audio cue, or LLM detection covers at least
  half of the gap, the two merge and the held gap marker is released. The
  merged marker spans both and keeps the other detection's stage.
- The validator also checks every other detection against the gaps. When a
  gap covers at least half of a detection's own length, that detection is
  marked `corroborated_by: transcript_differential` and its confidence rises
  by 0.1 (0.15 when the gap was `offset_confirmed`, capped at 0.95, never
  lowered).
- If no detection releases it, the gap stays its own held marker on the
  episode's Held for Review list, with the hold reason above, until you
  confirm or reject it.
- Rejecting a held gap marker clears it from that episode without seeding a
  cross-episode false-positive match: the span is real show content the
  publisher's transcript happened to omit, not a confirmed false positive from
  a real detector, so it should not suppress a genuine future catch on another
  episode.

The stage runs after transcription and before detection on every run that
performs detection, including a re-detection. A re-detection normally
re-fetches the publisher transcript fresh. The one exception: a reprocess
that reuses the previous run's saved Whisper segments (rather than
re-transcribing) also reuses a stored `ok` result for the same URL if it is
less than 24 hours old, since the word times it would realign against have
not moved. Any reprocess that re-transcribes always fetches fresh.

A fetch failure, a parse failure, or an alignment that lands below the
coverage floor all leave the run stats and the episode's `upstreamTranscript`
field with a status (`error` or `unreliable`) instead of blocking anything
else the run does.

## Settings

- **Compare with the publisher transcript** (Settings > Ad Detection) is the
  global default, on by default.
- Each feed's Advanced settings has its own **Transcript diff** select
  (Inherit / On / Off), next to **Cross-fetch diff**, that overrides the
  global default for that feed. See [Configuration > Ad Detection
  Settings](configuration.md#ad-detection-settings).

The API uses `transcriptDifferentialEnabled` on
`PUT /api/v1/settings/ad-detection` for the global default and
`transcriptDifferential` on `PATCH /api/v1/feeds/{slug}` for the feed override.
Send `true` for On, `false` for Off, or `null` to inherit on the feed.
`GET /api/v1/feeds/{slug}/episodes/{id}` returns the comparison result in
`upstreamTranscript`, including status, coverage, and detected gaps. The full
schemas are in [openapi.yaml](../openapi.yaml).

## Limits

- Only the Whisper transcript is diffed; there is no attempt to use the
  publisher's transcript as a replacement for Whisper, and it is never served
  to subscribers.
- No speaker diarization: cues and words are compared as plain text.
- The stage runs at processing time or at a re-detection. If a publisher
  posts their ad-free transcript after MinusPod already processed the
  episode, nothing re-triggers a recut automatically; a manual re-detection
  picks it up.
