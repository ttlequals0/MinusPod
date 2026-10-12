# Cross-episode scan UI: play buttons and per-episode match breakdown

Date: 2026-07-10
Source: issue #350, user feedback on 2.39.0,
plus maintainer follow-up: Held for Review rows need playback too.

## Problem

The Cross-episode scan results list (CueCrossEpisodeScanModal) shows each
discovered candidate as a time range plus an "in X of M eps" badge. The
feedback asks for:

1. A play button on each discovered candidate, so the user can hear the
   segment before making a template from it.
2. A per-episode metric: how many times each candidate is found in each
   episode, ideally enumerating every match in every episode in a
   collapsible list.
3. (Follow-up ask) The "Held for Review" section on the episode page lists
   held ads with Approve & Recut / Dismiss actions, but no way to listen
   to the held segment before deciding. It needs a play button like the
   Cue Matches section has.

Item 1 and 3 are frontend-only. Item 2 needs backend work: the scan
pipeline (_find_shared_segments -> discover_cross_episode_body) returns
only (start, end, sibling_match_count) per candidate. It does not record
which episodes matched, where in each episode, or multiple occurrences per
episode (only the best offset per sibling survives).

With three consumers of "play the [start, end] window of an episode's
retained original" (Cue Matches, the scan modal, Held for Review), the
proven CueDetectionsSection audition logic (autoplay-gesture preservation,
cold-load seek, superseded-click token) is extracted into a shared
useAuditionPlayer hook rather than duplicated.

## Approach (chosen)

Enrich the existing background scan. After _find_shared_segments produces
each candidate run, enumerate every occurrence of that run in every
episode's already-computed fingerprint array. No new endpoint, no new
fpcalc runs, no DB schema change (the scan result is a cached JSON blob).

Rejected alternative: a lazy per-candidate detail endpoint computed on
expand. It would need to re-fingerprint up to 5 full episodes per expand
(fpcalc is the slow part of the scan) or grow a fingerprint cache; far
more machinery for a worse UX.

## Design

### 1. Fingerprinter (src/audio_fingerprinter.py)

discover_cross_episode_body gains an occurrence-enumeration pass:

- For each candidate run [lo, hi] in the target fingerprint, slide that
  window across every episode's fingerprint array: the target itself and
  each usable sibling.
- Mechanism per episode: the existing cross-array pattern already used at
  the probe stage:
  `_window_similarity(np.concatenate([target[lo:hi], ep_arr]), 0, win)[win:]`
  then `_greedy_hit_positions(sim, similarity, min_gap)`.
- similarity: the same AUDIO_CUE_XEP_SIMILARITY the discovery pass used.
- min_gap: `max(1, round(AUDIO_CUE_FP_MIN_GAP_SECONDS * fps))`, the same
  gap _count_window_matches uses, computed with each episode's own fps.
- Hit positions convert to seconds with each episode's own
  fps = len(ints) / duration. Each match is emitted as
  {start, end} where end = start + window duration in that episode's
  timeline.
- Result shape per candidate:
  `episodes: [{index, matchCount, matches: [{start, end}]}]`
  where index 0 = target episode and 1..N = siblings in the caller's
  input order. Episodes with zero matches are included with
  matchCount 0 and empty matches, so the UI can show "not found".
- Siblings whose fingerprint failed to decode are dropped from
  enumeration but their original input index is preserved for the ones
  that survive (track (orig_index, fp) pairs when building sib_fps), so
  the index -> episodeId mapping never shifts.
- The target's own enumeration always finds at least the [lo, hi] run
  itself, so the target reports matchCount >= 1.

### 2. API layer (src/api/cue_templates.py)

_run_cue_cross_episode_scan maps enumeration indices to episode IDs:
index 0 -> target_episode_id, index i -> episode_ids[i]. The saved
payload's candidates gain:

    episodes: [{episodeId, matchCount, matches: [{start, end}]}]

Same cached row (cue_cross_episode_scan), same claim/poll lifecycle.
Old cached rows simply lack the field; no migration, no invalidation.

### 3. Frontend types (frontend/src/api/cueTemplates.ts)

    export interface CrossEpisodeCandidateEpisode {
      episodeId: string;
      matchCount: number;
      matches: { start: number; end: number }[];
    }

    // CrossEpisodeCandidate gains:
    episodes?: CrossEpisodeCandidateEpisode[];

Optional because older servers / pre-upgrade cached scans omit it.

### 3b. Shared audition hook (frontend/src/hooks/useAuditionPlayer.tsx)

Extract CueDetectionsSection's audition logic verbatim into a hook:

    const { playingKey, toggle, stop, audioElement } =
      useAuditionPlayer(preloadSrc?);
    toggle(key, src, startS, endS)  // play or, if key is playing, pause

- One hidden <audio> element (returned as audioElement, rendered by the
  consumer); one row sounds at a time.
- Preserves the existing behaviors: play() called synchronously on click
  to keep the autoplay gesture; seek deferred to loadedmetadata on cold
  load; a request token so a superseded click's callbacks bail; timeupdate
  listener pauses at the window end; onPause/onEnded reset state.
- Multi-source: toggle() takes the src per call (the scan modal plays
  different episodes). Changing src resets the element and takes the
  cold-load path. Optional preloadSrc pins the element's src attribute
  with preload="metadata" for single-source consumers (Cue Matches,
  Held for Review), matching today's behavior.
- CueDetectionsSection is refactored onto the hook with no behavior
  change (key = String(detection.id)).

### 4. UI (frontend/src/components/CueCrossEpisodeScanModal.tsx)

Candidate row:

- Play/stop button before the timestamps. Plays the target episode's
  `/api/v1/feeds/{slug}/episodes/{id}/original.mp3` seeked to c.start,
  stopping at c.end, via useAuditionPlayer (key `episodeId:start:end`).
- Chevron toggle after the badge, shown only when c.episodes is present.
  Expands a per-episode breakdown under the row.

Expanded breakdown, one line per episode in scan order:

- Episode title, resolved from `selected` / picker metadata, falling
  back to a getEpisode fetch for IDs the modal does not hold (the same
  fallback pattern the modal already uses for targetEp). While loading,
  show the episode id.
- "N matches" (or "not found" when matchCount is 0).
- Each match rendered as a monospace `start - end` chip with its own
  small play button that plays that episode's original.mp3 over the
  match window, through the same shared audio element.

### 4b. Held for Review playback (frontend/src/pages/EpisodeDetail.tsx)

Each held row (pendingReviewMarkers) gets a play/pause button before its
timestamps, visually matching the Cue Matches row button, playing
[segment.start, segment.end] of the episode's original.mp3 through
useAuditionPlayer (key `held-{start}-{end}`, preloadSrc = the episode's
original URL). Held-ad marker times are in the original-audio timeline
(held ads are never cut), so the original is the correct source. The
button renders only when episode.hasOriginalAudio is true, without the
retained original there is nothing to play (the section already handles
this state for Approve & Recut).

Degradation: when c.episodes is absent (cached scan from a pre-upgrade
server run), no chevron is rendered and a single hint line appears once
above the list: "Rescan to see per-episode matches." The existing
Rescan button performs the upgrade.

The existing "in X of M eps" badge is unchanged.

### 5. Error handling

- Audio element errors (e.g. an original aged out after the scan) stop
  playback silently and reset the button to its idle state, matching
  CueTemplatesPanel behavior.
- Fingerprint-decode failures already degrade the scan to fewer
  siblings; enumeration inherits that (dropped siblings report via the
  preserved-index mapping only if they were enumerable; a sibling with
  no fingerprint is included with matchCount 0).
- The enumeration pass is pure numpy over in-memory arrays; any
  unexpected exception in it must not kill the scan, wrap it so the
  candidate list still saves without `episodes` rather than erroring
  the whole row.

### 6. Testing

- pytest (tests/, synthetic fingerprint arrays, alongside existing
  discover_cross_episode_body tests):
  - candidate present in all episodes: per-episode counts and positions
    correct, target reports its own run.
  - candidate repeating twice in one sibling: matchCount 2 with two
    distinct, gap-separated matches.
  - sibling with zero matches: included with matchCount 0.
  - sibling whose fingerprint fails to decode: index mapping of the
    remaining siblings unshifted.
- API-layer test: saved payload maps indices to episode IDs correctly.
- Held for Review (EpisodeDetail.test.tsx): play button renders per held
  row when hasOriginalAudio is true; absent when false. Existing Cue
  Matches behavior guarded by the full vitest suite after the hook
  refactor.
- Frontend: `cd frontend && npx tsc --noEmit`.
- Full suite: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`.
- Real-data verification (issue #350 thread provides ground truth):
  - The transition sting exported in the thread recurs ~5-7x per
    episode per the reporter; a cross-episode scan over that show's
    episodes should surface it with per-episode matchCounts in that
    range, each with plausible timestamps.
  - On a server with feeds that have known cues, run a scan after deploy
    and spot-check the breakdown and both play paths (candidate row and
    per-match chips).

### 7. Release mechanics

- Feature branch off main (never commit to main).
- version.py bump + matching openapi.yaml version; openapi.yaml gains
  the new `episodes` field on the cross-episode scan response schema.
- CHANGELOG.md entry.

## Out of scope

- Waveform preview of candidates.
- Any change to candidate discovery itself (thresholds, window growth).
- The other asks tracked in issue #350 (audio-cue detection timing).
