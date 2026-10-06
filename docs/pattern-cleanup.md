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

Review suggestions in the Cleanup tab on the Patterns page. An approved change keeps the pattern's prior state, so it can be undone later. Nothing in the pipeline reads a suggestion before you act on it: pattern matching, detection, and cutting behave exactly as before this feature.

## Scope: learned patterns only

Pattern Cleanup only reviews patterns where `created_by = 'auto'` and `source = 'local'` and the pattern is active, meaning patterns MinusPod itself learned from your episodes. Community patterns (synced from the shared manifest) and manually created patterns are never touched or reviewed, even while cleanup is enabled.

## Schedule and model choice

Settings > Experiments > Pattern Cleanup has:

- **Enable scheduled cleanup** - turns the cron schedule on or off. Run now works either way.
- **Schedule** - a 5-field cron expression, interpreted as UTC. Default `0 4 * * 0` (weekly, Sunday 04:00).
- **Cleanup Provider** / **Cleanup Model** - which credential slot and model review the patterns. Left blank, both inherit the detection stage's slot and model. The UI recommends picking a higher-quality model here than you use for everyday detection: cleanup runs far less often than detection, so the extra cost per call buys more reliable trims and splits.
- **Patterns per run** - how many patterns one run sends to the model, 1 to 200, default 25. Patterns never reviewed go first, then patterns whose reviews failed. Statistics checks cover all active learned patterns without model calls.
- **Retire after (days unused)** - how long without a match before cleanup suggests retiring a pattern, 7 to 3650 days, default 90.

Save configuration changes before starting a run. Run now and Force recheck
all use the saved provider, model, and batch size.

Turning the schedule on never starts a run right away. Each time it goes from off to on, the next scheduled run waits for the first cron slot after that moment, even when the last run was weeks ago. Enabling does not change the Last run time on the card. **Run now** is unaffected and always starts a run right away, subject to the usual lock (see below).

A run starts in a background thread and the API call returns as soon as it has been queued; the Settings card polls while a run is in progress and updates the last-run line (reviewed, suggested, skipped, and any error) once it finishes. Only one run can hold the lock at a time; starting another while one is active returns an in-progress error instead of queueing a second run.

Cleanup follows the selected provider's failover state between model calls.
Cancelling failover or recovering the original provider takes effect on the
next review. The failover provider uses its detection model for cleanup.
Run-history `model`, `provider`, and `credentialSlot` describe the configured
route captured at the start. Individual request usage records show the
provider actually called, including failover retries.

## How suggestions are produced

Each run checks active learned patterns for two conditions without calling the model:

- **Retire**: the pattern has had no match in longer than the configured unused-days window (or never matched), and it is old enough that this isn't just a freshly learned pattern waiting for its first match.
- **Flag**: the pattern already has a high false-positive rate relative to its confirmations, the same rule that would otherwise just sit in the pattern's stats unexamined.

A false-positive flag requires at least two false positives and at least as
many false positives as confirmations. These checks continue after the model
has reviewed unchanged text and sponsor, and still work when the model is unavailable.
Rejecting a statistical suggestion acknowledges that evidence: the same
inactivity period or false-positive count is not proposed again. A new false
positive, a new match followed by another unused period, changed pattern text,
or a changed retirement threshold can produce a new suggestion. Improving
confirmation counts alone does not repeat a dismissed false-positive flag.

For patterns due for model review, it then asks the model to review the text.
When the episode the pattern was learned from still has its original
transcript retained, the prompt includes about 45 seconds of transcript on
each side of the pattern's location, with the pattern's own span marked.
This context is saved with the suggestion for human review. Without a
retained transcript, the model works from the pattern text by itself.

The model's answer goes through a validation gate before anything is stored:

- Any text it proposes keeping must be a close match to a real contiguous slice of the original pattern text (a near-substring, not just similar wording).
- A trim must remove at least 5 words or 10% of the original. A smaller trim is ignored, but a valid sponsor correction can still become a rename suggestion.
- A split's pieces must not overlap, and each piece must mention its own sponsor.
- A trim must keep the recorded sponsor when the original text names it. If the sponsor is absent or unknown, it must retain a name from the sponsor list. A combined trim and sponsor correction can instead use the validated new sponsor in the retained text.
- A rename must name a sponsor that is both present in the text and a valid sponsor name. The podcast's own title or slug is never accepted as a sponsor.
- A trim can include a sponsor correction in the same proposal. The corrected sponsor must occur in the retained text; approval and undo apply both changes together.
- Confidence must be a finite number from 0 to 1. Confidence below 0.5 is dropped.

A pattern is parked after three failed reviews in a row across runs, whether the gate rejected the answer or the call itself failed. Later runs skip it until you force a recheck. A failed pattern also moves to the back of the queue, so it cannot hold up the rest of the batch. Three failed review calls in a row within one run stop that run as failed, since that usually means the provider or model setting is wrong.

The sponsor check verifies that the name occurs in the retained text. It cannot distinguish an advertisement from a passing mention, so inspect the kept text before approving.

## The five kinds

| Kind | What it proposes | What approving does |
|---|---|---|
| Trim | Removes leading and/or trailing text that isn't the sponsor read, optionally correcting its sponsor | Replaces the text and intro/outro variants, plus the sponsor when included |
| Split | Two or more sponsors read back to back become separate patterns | Creates the new patterns and disables the original, with a reason noting the split |
| Rename | The sponsor on the pattern doesn't match what's actually read | Re-resolves the sponsor and updates the pattern's sponsor link |
| Retire | The pattern hasn't matched in a long time | Deactivates the pattern with a reason noting how long it went unused |
| Flag | High false-positive rate, or content the model judges contaminated beyond a trim | Deactivates the pattern (or applies a trim, when the model's recommendation is a trim rather than disabling) |

Each card shows the suggestion's confidence and reasons. Statistics-only suggestions are generated without a model call.

Contamination remains visible even when the model also proposes an edit.
When the model says a trim cannot fix the contamination, cleanup retains a
disable recommendation rather than presenting the trim as a clean pattern.

## Approve, reject, undo

Expand **Original pattern text** on any card to inspect the source of the
proposal. **Source context** appears when a retained transcript was available.
Trims show removed and retained text; combined edits also show the sponsor
change. Use **Load older suggestions** to reach older decisions, including
approvals you want to undo. Bulk actions apply to the selected loaded rows.

**Approve** applies the change in one transaction and saves the previous and
resulting states. It refuses to overwrite fields changed since review,
including manually edited intro/outro variants. **Reject** leaves the pattern
untouched and acknowledges the reviewed version; rejecting an outdated
suggestion returns a conflict instead of marking newer text reviewed.
Successful model reviews are not repeated for unchanged text and sponsor unless you force
a recheck. Statistics checks can still surface new evidence as described above.

**Undo** restores the pattern from that snapshot: a trim or flag-trim restores the text, a rename restores the sponsor, and retire or a flag-disable restores active status. A split's undo re-enables the original pattern and disables the pieces that were created from it. Undo is scoped to exactly the fields its own kind changed, so undoing a trim never touches a sponsor rename applied separately.

Undo is refused once a later suggestion has been approved against the same
pattern. Undo the later approval first. It also refuses to overwrite fields
edited after approval, including split children that were manually corrected.
Older approvals without enough saved state for a safe undo return a conflict.

## Force recheck

**Force recheck all** resets review eligibility for every active learned
pattern, including patterns with approved or rejected suggestions. It also
replaces all their pending suggestions, including those outside the first
batch. Approved, rejected, and undone decision records are retained. Disabled,
manual, and community patterns are excluded.

Click Force once, then use **Run now** or leave scheduling enabled to process
the remaining batches. Progress survives a server restart. Clicking Force
again starts another sweep from the beginning. The confirmation dialog warns
that pending suggestions will be replaced.

## API

All routes are under `/api/v1`, and every `POST`/`PUT` needs the `X-CSRF-Token` header once a password is set.

| Route | Purpose |
|---|---|
| `GET /patterns/cleanup` | Settings plus live state: `inProgress`, `lastRun` (start of the newest run), `lastSummary` and `lastError` (newest finished run), pending counts by kind |
| `PUT /settings/pattern-cleanup` | Update the six settings |
| `POST /patterns/cleanup/run` | Start a run (`{"force": true}` to force); 202 with `{"runId"}`, 409 if one is already running, rate limited to 6/hour |
| `GET /patterns/cleanup/runs` | Recent runs, newest first |
| `GET /patterns/cleanup/suggestions` | List suggestions, filterable by `status` and `kind`; paginate with `limit` (1-200, default 50) and `offset` |
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

Run requests may omit the body. If supplied, the body must be a JSON object
and `force` must be a boolean. Settings updates require a non-empty object;
`enabled` must be a boolean, `cron` a string, and numeric settings integers.
Malformed requests return HTTP 400 before starting work or saving any fields.
Stale approvals, rejections, and unsafe undo operations return HTTP 409.

Suggestion payloads use camelCase. A trim may include `payload.sponsor` for a
combined correction; a flag recommending a trim uses `trimText` and may also
include `sponsor`. `before.textTemplate` is the original pattern text, and
optional `before.sourceContext` is the real retained transcript context.

## Settings reference

All settings are under `PUT /api/v1/settings/pattern-cleanup`; database-only, no environment variable backing.

| Payload key | Kind / range | Default |
|---|---|---|
| `enabled` | bool | `false` |
| `cron` | 5-field cron expression, UTC | `0 4 * * 0` |
| `batchSize` | int, 1-200 | `25` |
| `unusedDays` | int, 7-3650 | `90` |
| `provider` | `primary`, `secondary`, or `same_as_detection`; `a`/`b` aliases accepted; blank or null inherits detection's slot | unset |
| `model` | string, up to 200 characters; blank or null inherits the detection model | unset |

The system prompt the model receives is editable under Settings > Prompts ("Pattern Cleanup Prompt") like the other pipeline prompts, with its own reset-to-default control.

---

[< Docs index](README.md) | [Project README](../README.md)
