# Pattern Cleanup

[< Docs index](README.md) | [Project README](../README.md)

---

Experimental. Off by default. Nothing it suggests changes a pattern until you approve it.

## Contents

- [What it does](#what-it-does)
- [Scope: learned patterns only](#scope-learned-patterns-only)
- [Schedule and model choice](#schedule-and-model-choice)
- [How suggestions are produced](#how-suggestions-are-produced)
- [The five kinds](#the-five-kinds)
- [Approve, reject, undo](#approve-reject-undo)
- [Force recheck](#force-recheck)
- [API](#api)
- [Settings reference](#settings-reference)

## What it does

A learned pattern is captured straight from a transcript, so it often carries a few words of host banter or show content stitched onto the actual sponsor read, or merges two sponsors read back to back into one pattern. Pattern Cleanup runs an LLM over your learned pattern library and proposes a trim, a split, a sponsor rename, or retiring a pattern that has stopped matching, so each pattern ends up as the exact ad copy and nothing else.

Every suggestion sits in the Cleanup tab on the Patterns page until you approve or reject it. An approved change keeps the pattern's prior state, so it can be undone later. Nothing in the pipeline reads a suggestion before you act on it: pattern matching, detection, and cutting behave exactly as before this feature.

## Scope: learned patterns only

Pattern Cleanup only reviews patterns where `created_by = 'auto'` and `source = 'local'` and the pattern is active, meaning patterns MinusPod itself learned from your episodes. Community patterns (synced from the shared manifest) and manually created patterns are never touched or reviewed, even while cleanup is enabled.

## Schedule and model choice

Settings > Experiments > Pattern Cleanup has:

- **Enable scheduled cleanup** - turns the cron schedule on or off. Run now works either way.
- **Schedule** - a 5-field cron expression, interpreted as UTC. Default `0 4 * * 0` (weekly, Sunday 04:00).
- **Cleanup Provider** / **Cleanup Model** - which credential slot and model review the patterns. Left blank, both inherit the detection stage's slot and model. The UI recommends picking a higher-quality model here than you use for everyday detection: cleanup runs far less often than detection, so the extra cost per call buys more reliable trims and splits.
- **Patterns per run** - how many patterns one run reviews, 1 to 200, default 25. Patterns never reviewed go first, then the ones reviewed longest ago.
- **Retire after (days unused)** - how long without a match before cleanup suggests retiring a pattern, 7 to 3650 days, default 90.

Turning the schedule on never starts a run right away. Each time it goes from off to on, the next scheduled run waits for the first cron slot after that moment, even when the last run was weeks ago. Enabling does not change the Last run time on the card. **Run now** is unaffected and always starts a run right away, subject to the usual lock (see below).

A run starts in a background thread and the API call returns as soon as it has been queued; the Settings card polls while a run is in progress and updates the last-run line (reviewed, suggested, skipped, and any error) once it finishes. Only one run can hold the lock at a time; starting another while one is active returns an in-progress error instead of queueing a second run.

## How suggestions are produced

For each candidate pattern, cleanup first checks two things without calling the model:

- **Retire**: the pattern has had no match in longer than the configured unused-days window (or never matched), and it is old enough that this isn't just a freshly learned pattern waiting for its first match.
- **Flag**: the pattern already has a high false-positive rate relative to its confirmations, the same rule that would otherwise just sit in the pattern's stats unexamined.

It then asks the model to review the pattern text. When the episode the pattern was learned from still has its original transcript retained, the prompt includes about 45 seconds of transcript on each side of the pattern's location, with the pattern's own span marked. That lets the model see where the sponsor read actually starts and ends, rather than guessing from the pattern text alone. Without a retained transcript, the model works from the pattern text by itself.

The model's answer goes through a validation gate before anything is stored:

- Any text it proposes keeping must be a close match to a real contiguous slice of the original pattern text (a near-substring, not just similar wording).
- A trim must actually remove a meaningful amount of text, at least 5 words or 10% of the original, or it is treated as a no-op.
- A split's pieces must not overlap, and each piece must mention its own sponsor.
- When the pattern's text does not name its recorded sponsor (or it has none), a trim must keep the name of a sponsor already in the sponsor list.
- A rename must name a sponsor that is both present in the text and a valid sponsor name. The podcast's own title or slug is never accepted as a sponsor.
- Confidence below 0.5 is dropped.

A pattern is parked after three failed reviews in a row across runs, whether the gate rejected the answer or the call itself failed. Later runs skip it until you force a recheck. A failed pattern also moves to the back of the queue, so it cannot hold up the rest of the batch. Three failed review calls in a row within one run stop that run as failed, since that usually means the provider or model setting is wrong.

**Known limit**: the gate checks sponsor presence by searching the proposed text for the sponsor's name, not by checking that the name sits inside the actual sponsor copy. A mechanical substring check cannot tell "this is the sponsor being advertised" apart from "this sponsor's name happens to appear here." If a sponsor's name survives only in a passing aside the model kept alongside the real read, rather than in the ad copy itself, the gate accepts it anyway. Review a suggestion's kept text before approving if the pattern's wording is unusual.

## The five kinds

| Kind | What it proposes | What approving does |
|---|---|---|
| Trim | Removes leading and/or trailing text that isn't the sponsor read | Replaces the pattern's text and rederives its intro/outro variants |
| Split | Two or more sponsors read back to back become separate patterns | Creates the new patterns and disables the original, with a reason noting the split |
| Rename | The sponsor on the pattern doesn't match what's actually read | Re-resolves the sponsor and updates the pattern's sponsor link |
| Retire | The pattern hasn't matched in a long time | Deactivates the pattern with a reason noting how long it went unused |
| Flag | High false-positive rate, or content the model judges contaminated beyond a trim | Deactivates the pattern (or applies a trim, when the model's recommendation is a trim rather than disabling) |

Each suggestion carries the model's confidence and a short list of reasons, shown on its card in the Cleanup tab.

## Approve, reject, undo

**Approve** applies the change to the pattern in place and keeps a snapshot of the pattern's prior state. It is refused if the pattern's text or sponsor changed after the suggestion was made. **Reject** leaves the pattern untouched and marks it reviewed, so it is skipped on later runs unless the pattern's text changes or you force a recheck.

**Undo** restores the pattern from that snapshot: a trim or flag-trim restores the text, a rename restores the sponsor, and retire or a flag-disable restores active status. A split's undo re-enables the original pattern and disables the pieces that were created from it. Undo is scoped to exactly the fields its own kind changed, so undoing a trim never touches a sponsor rename applied separately.

Undo is refused once a later suggestion has been approved against the same pattern. Approving a second change commits to the pattern's new state, and undoing an earlier one out from under it would leave the two approvals in conflict. To undo the earlier one, undo the later approval first. Undo is also refused when the field it would restore was edited after the approval.

## Force recheck

**Force recheck all** reviews every learned pattern again, including ones already approved or rejected, ignoring the reviewed-hash skip that normally keeps a run from re-examining unchanged patterns. A forced run also supersedes every pending suggestion outstanding at the time it runs, including ones from a kind the new review doesn't touch. Once you force a recheck, the old queue of pending suggestions is cleared and replaced by whatever the new pass produces. It still reviews patterns in batches of the configured size, so a large pattern library takes several runs.

## API

All routes are under `/api/v1`, and every `POST`/`PUT` needs the `X-CSRF-Token` header once a password is set.

| Route | Purpose |
|---|---|
| `GET /patterns/cleanup` | Settings plus live state: `inProgress`, `lastRun` (start of the newest run), `lastSummary` and `lastError` (newest finished run), pending counts by kind |
| `PUT /settings/pattern-cleanup` | Update the six settings |
| `POST /patterns/cleanup/run` | Start a run (`{"force": true}` to force); 202 with `{"runId"}`, 409 if one is already running, rate limited to 6/hour |
| `GET /patterns/cleanup/runs` | Recent runs, newest first |
| `GET /patterns/cleanup/suggestions` | List suggestions, filterable by `status` and `kind` |
| `POST /patterns/cleanup/suggestions/{id}/approve` | Approve one suggestion |
| `POST /patterns/cleanup/suggestions/{id}/reject` | Reject one suggestion |
| `POST /patterns/cleanup/suggestions/{id}/undo` | Undo an approved suggestion |
| `POST /patterns/cleanup/suggestions/bulk` | Approve or reject several ids at once |

```bash
# Current status, settings, and pending counts
curl -s http://your-server:8000/api/v1/patterns/cleanup \
  -H "Cookie: $COOKIE"

# Start a run now
curl -s -X POST http://your-server:8000/api/v1/patterns/cleanup/run \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" -d '{"force": false}'

# List pending trims
curl -s "http://your-server:8000/api/v1/patterns/cleanup/suggestions?status=pending&kind=trim" \
  -H "Cookie: $COOKIE"

# Approve a suggestion
curl -s -X POST http://your-server:8000/api/v1/patterns/cleanup/suggestions/42/approve \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF"
```

See [`openapi.yaml`](../openapi.yaml) for the full request and response schemas (`PatternCleanupStatus`, `PatternCleanupRun`, `PatternCleanupSuggestion`).

## Settings reference

All settings are under `PUT /api/v1/settings/pattern-cleanup`; database-only, no environment variable backing.

| Payload key | Kind / range | Default |
|---|---|---|
| `enabled` | bool | `false` |
| `cron` | 5-field cron expression, UTC | `0 4 * * 0` |
| `batchSize` | int, 1-200 | `25` |
| `unusedDays` | int, 7-3650 | `90` |
| `provider` | `primary`, `secondary`, or `same_as_detection`; blank inherits detection's slot | unset |
| `model` | string, up to 200 characters; blank inherits the detection model | unset |

The system prompt the model receives is editable under Settings > Prompts ("Pattern Cleanup Prompt") like the other pipeline prompts, with its own reset-to-default control.

---

[< Docs index](README.md) | [Project README](../README.md)
