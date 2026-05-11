# Corpus

`data/corpus/` holds verified episodes used as benchmark input. Each episode lives under `data/corpus/<ep-id>/` with these files:

```
data/corpus/<ep-id>/
  metadata.toml      # podcast/episode identity, source, hashes
  segments.json      # original Whisper segments, byte-exact from the original-segments endpoint
  truth.txt          # human-authored ground truth
  windows.json       # precomputed windows + transcript_lines (frozen)
```

`data/candidates/` is gitignored and holds in-progress captures before verification.

## How to verify a candidate

After `benchmark capture --episode-url <url>` writes `data/candidates/<ep-id>/`:

1. Open `data/candidates/<ep-id>/truth.txt`. The capture pre-populated entries from production ad markers.
2. For each pre-populated ad block:
   - Confirm `start:` and `end:` against the audio. MinusPod's production ad markers are usually right but not perfect.
   - Confirm `text:` matches what the segments actually say in that range. Edit if the auto-extract pulled the wrong window.
3. If the capture pre-populated rejected markers (commented out at the bottom), uncomment and clean up any that are actually real ads.
4. If the episode has no ads, replace everything with a single line: `# Verified: no ads in this episode.`
5. Run `benchmark verify <ep-id>`. If validation fails, fix the file and retry. On success the directory moves to `data/corpus/`.

## truth.txt format

```
# comment lines start with '#'
start: 0:45
end: 1:52
text: This episode is brought to you by BetterHelp...
multi-line text continues until the next start: or ---
---
start: 20:40
end: 21:45
text: Acast powers the world's best podcasts.
```

- Time formats: `mm:ss`, `m:ss`, `h:mm:ss`, optional decimal seconds.
- Labels are case-insensitive: `start`, `end`, `text` are all required per block.
- `text:` continues until the next `start:` or `---`. No escaping; embedded `:` is fine.
- Empty file fails validation. For a no-ad episode use the marker line (above).

## Reviewer cheat sheet

These rules mirror MinusPod's ad-detection prompt, so ground truth and predictions are comparing apples to apples.

- **Include transition phrases at the start** of an ad block (`Let's take a quick break...`, `This episode is brought to you by...`).
- **End at the final URL or code mention** before content resumes.
- **Merge ads with gaps under 15 seconds.** Two consecutive sponsor reads with a brief pause are a single ad block.
- **Stand-alone podcast promos count as ads.** "Here's a show we recommend..." reads.
- **Do not include intros, content recaps, or post-roll thank-yous** unless they advertise something.
- When in doubt about a borderline ad, prefer the stricter (smaller) range -- false positives in ground truth poison every model's score.

## Hashing

`metadata.toml` carries a sha256 of `segments.json`. `corpus.load_episode` re-hashes on load; if it doesn't match, the episode fails to load until you re-capture or update the metadata. This catches accidental segment edits.

## Re-running with a new windowing config

If MinusPod's `create_windows` parameters change (window size, overlap), `windows.json` becomes stale. Regenerate per episode:

```sh
benchmark regenerate-windows <ep-id> --force
```

Calls already in `calls.jsonl` with the old `prompt_hash` stay there; new prompts now hash differently and will execute on the next `benchmark run`.
