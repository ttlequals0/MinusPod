# Parallel processing against a remote Whisper backend

Status: implemented in 2.96.2.

## Goal

Let an instance whose `whisper_backend` is `openai-api` run more than one
episode at a time and more than one transcription request at a time, with
the total number of in-flight Whisper requests bounded by an operator
setting. Everything is configured at runtime through Settings and the API.

The whole feature sits behind one opt-in toggle, `whisper_pool_enabled`,
off by default, and only takes effect when `whisper_backend` is
`openai-api` as well. With the toggle off, or on the local backend, every
code path behaves exactly as it does today: one run at a time, runs start
wherever they start now, the chunk executor is sized by
`transcribe_concurrent_chunks` alone, and no pool exists. An install that
upgrades and changes nothing sees no change. No value in this design is
specific to one deployment; capacity is whatever the operator says it is.

"Pool active" below means: toggle on and backend `openai-api`. Everything
under Design that changes behavior is conditional on it unless the text
says otherwise.

Every existing way to start processing keeps working: auto-process from feed
refresh, Play (JIT) from a podcast app, Reprocess and mode reprocess from the
episode page, pending-recut apply, bulk recut, and local-feed import. What
changes is where the run executes, not whether it runs.

## The constraint today

Processing is single-slot in nine places. The first four are the ones a
design doc would spot; the rest only show up in the code.

| Where | Assumption |
|---|---|
| `ProcessingQueue` (`src/processing_queue.py`) | one flock, one `current_episode` in the state file, `get_current()` returns one tuple, `acquire` rejects a second episode in the same process |
| `background_queue_processor` (`src/main_app/background.py`) | claims one row, then blocks polling until that run ends |
| `whisper_api_base_url` | one URL, no capacity |
| `_transcribe_chunked_parallel_api` (`src/transcriber.py`) | a per-call thread pool of `transcribe_concurrent_chunks` workers with no shared bound, so in-flight requests = episodes x chunks |
| `StatusService` (`src/status_service.py`) | one `current_job`; `update_job_stage`, `complete_job`, `fail_job` take no episode key |
| `llm_client._EpisodeAccumulator` | module global; the comment says its correctness rests on the flock |
| `run_log._active_recorder` | module global, set and cleared per run |
| `reset_stuck_processing_episodes` (`src/main_app/background.py`) | every processing row except the one current tuple is treated as orphaned after 30 minutes |
| `start_background_processing` callers | Play, Reprocess, pending-recut apply and bulk recut start runs inside the API worker that took the request, so runs live in more than one process |

The last row is the one that shapes the design. A Whisper budget shared by
runs in different processes needs a cross-process counter, and every
per-run global above would need the same treatment.

## Design

### One process owns every run (pool active)

While the pool is active, the background leader (the gunicorn worker that
wins `_try_become_background_leader`) is the only process that starts
processing. API callers enqueue. Every caller already has that path: it is
what they do today when `start_background_processing` returns
`queue_busy`; with the pool active, that is the branch they always take.

`start_background_processing` keeps its signature and its return contract.
With the pool active and outside the leader it returns
`(False, "queue_only")` without touching the lock. With the pool inactive
it behaves exactly as today and starts the run in the calling process.
Callers do not change their fallback code.

Cost while active: a Play on an idle instance starts on the dispatcher's
next pass instead of in the request thread. The idle poll interval drops
from the current 30 s backoff to 5 s, so the added latency is at most 5 s
and usually less. A Play on a busy instance is unchanged: it was already
queued with the manual boost.

What this buys: the Whisper budget is a `threading.BoundedSemaphore`, the
status service is a dict in one process behind its existing file lock, and
the per-run globals become thread-scoped. None of them needs a
cross-process protocol.

### Settings

Three new registry entries in `src/database/settings.py`, following
`SettingSpec` so they get env override, DB persistence, payload mapping
and the ad-reset set:

| Key | Env | Payload key | Range | Default |
|---|---|---|---|---|
| `whisper_pool_enabled` | `WHISPER_POOL_ENABLED` | `whisperPoolEnabled` | bool | false |
| `whisper_pool_max_requests` | `WHISPER_POOL_MAX_REQUESTS` | `whisperPoolMaxRequests` | 1..64 | 4 |
| `whisper_pool_max_episodes` | `WHISPER_POOL_MAX_EPISODES` | `whisperPoolMaxEpisodes` | 1..16 | 1 |

`whisper_pool_max_requests` is how many transcription requests the backend
accepts at once; `whisper_pool_max_episodes` is how many episodes MinusPod
runs at once against it. Both are operator knowledge about their own
backend and hardware; MinusPod never guesses either.

Validation goes in `_apply_transcribe_chunk_fields` beside
`transcribeConcurrentChunks`. All three appear in `GET /settings`.

The two numbers are stored whatever the toggle says, so an operator can
set them before switching on, but they are read only while the pool is
active. `whisper_backend=local` keeps the pool inactive regardless of the
toggle, because the model lives in the process's own GPU memory and
`WhisperModelSingleton` is global state. The capacity endpoint reports
which of the two conditions is unmet so the UI can say why the dials are
inert.

The existing `whisper_api_base_url` stays a single URL. A load balancer in
front of replicas is the deployment shape this design targets; MinusPod
needs only the number of requests it may have in flight. An endpoint list
with client-side health checks is out of scope.

### WhisperPool

`src/whisper_pool.py`, one module-level instance.

- `active`: toggle on and backend `openai-api`. When inactive, `slot()` is
  a no-op context manager and `chunk_workers()` returns
  `transcribe_concurrent_chunks` unchanged, so the transcriber's call sites
  need no branch of their own and the inactive path is today's code.
- `capacity`: `whisper_pool_max_requests` while active, else 0.
- `acquire()` / `release()`: a `BoundedSemaphore(capacity)`. Every HTTP
  call in `_transcribe_via_api` runs inside `with pool.slot():`. The
  single-file path (`transcribe()` on the API backend) uses the same slot,
  so a short episode counts against the budget like a chunk does.
- `chunk_workers()`: the per-episode `ThreadPoolExecutor` size for
  `_transcribe_chunked_parallel_api`. While active it is
  `min(transcribe_concurrent_chunks, max(1, capacity // transcribing))`
  where `transcribing` is the number of episodes currently inside a
  transcription stage (the pool counts them on enter and exit). Every
  transcribing episode therefore keeps at least one worker and the sum of
  workers never exceeds capacity, which is the "episodes first" policy:
  throughput over single-episode latency, no starvation.
- `resize(new_capacity)`: called from the settings PATCH path through a
  version counter the pool checks on every `acquire`. Growing releases the
  difference into the semaphore. Shrinking records a debt that is paid as
  permits are released, so a permit already held is never revoked. A
  transcription in flight keeps its worker count; the next transcription
  computes a fresh one. Turning the toggle off is a resize to inactive:
  runs in flight finish under the pool, new runs take the inactive path.
- `snapshot()`: capacity, in-flight requests, transcribing episodes,
  effective chunk workers, for the capacity endpoint and the status
  payload.

`_get_whisper_settings` and `_get_chunk_settings` are read once per
transcription and passed down, instead of once per chunk. The pool reads
the backend and concurrency through the same short-TTL settings cache
`llm_client` uses.

### Whisper 429 handling

While the pool is active, `_transcribe_via_api` treats an HTTP 429 as
"wait, then retry this attempt", honoring `Retry-After` when present and
otherwise the existing backoff, up to the request timeout. It is not a
chunk failure and does not count against the 20 percent chunk failure
budget. A backend that answers 429 is a backend whose capacity was set too
high, and the fix is the setting, not a failed episode. With the pool
inactive a 429 is handled as it is today.

### Queue and dispatcher

`ProcessingQueue` becomes a slot registry in the same state file under the
same flock. This refactor applies whether or not the pool is active; with
a limit of 1 it behaves as the single lock does today.

- State: `{"slots": {"<slug>:<episode_id>": {"started_at": float, "pid": int}}}`.
- `acquire(slug, episode_id, limit)`: under the flock, refuses when the
  key is present or when `len(slots) >= limit`, else inserts.
- `release(slug, episode_id)`: removes the key. `release()` with no
  arguments is gone; every caller names its episode.
- `get_current()` returns a list of `(slug, episode_id)` tuples, oldest
  first. `is_processing(slug, episode_id)` scans it.
- Stale clearing checks each slot's `started_at` against the hard timeout
  and removes only that slot.
- The flock stays. It is what keeps the state file consistent when an
  API worker reads `is_processing` for a 409 or a feed delete while the
  leader writes.

`background_queue_processor` becomes a dispatcher:

- Each pass reads the effective episode limit: `whisper_pool_max_episodes` while
  the pool is active, else 1. With a limit of 1 the pass claims one row and
  waits for it, which is today's loop.
- While there is room and no rate-limit hold, it
  counts running workers, claims the next row and hands it to a worker
  thread that runs today's
  per-episode body: gate checks, `start_background_processing`, the
  completion poll, and the queue-row close.
- The pass then waits 5 s when nothing was claimed (30 s backoff as today
  while inactive), or continues at once when something was, and runs the
  existing maintenance block on its existing 10-pass cadence. The maintenance block runs once per pass in
  the dispatcher, never in a worker.
- Shrinking `whisper_pool_max_episodes` stops new claims until the running
  count fits. No run is interrupted.
- The rate-limit hold gate is unchanged: while the hold is active nothing
  is claimed; a 429 inside a run releases that run's row in place.

`reset_stuck_processing_episodes` skips every key in `get_current()`.

### Per-run state

These refactors apply whether or not the pool is active; with one job they
produce today's payloads and totals.

- `StatusService`: `current_job` becomes `jobs`, a dict keyed by
  `slug:episode_id`, in the same file. `start_job`, `update_job_stage`,
  `complete_job`, `fail_job` and `clear_if_matches` take the key. Every
  call site in `src/main_app/processing.py` passes it (they all have
  `slug` and `episode_id` in scope). `_expire_stale` applies per job.
  `reconcile_startup_state` resets every job in the file.
- `llm_client._EpisodeAccumulator` becomes a `threading.local`, so each
  worker thread accumulates its own totals. `run_in_worker_thread` already
  exists for chunk threads; the chunk threads of one episode report into
  that episode's accumulator through the same handoff the run log uses.
- `run_log._active_recorder` becomes thread-scoped the same way.
- `cancel` events are already keyed by episode; no change.
- Transcriber log lines gain the `[slug:episode_id]` prefix the rest of the
  pipeline uses, so two episodes' chunk logs can be told apart.
- `clear_gpu_memory` and `WhisperModelSingleton.unload_model` are only
  reached on the local backend, which stays single-slot.

### API

- `GET /status` and the SSE stream: `jobs` (list, oldest first, each with
  the fields `currentJob` has today) plus `whisper` (the pool snapshot).
  `currentJob` stays and is `jobs[0]` or null, so nothing that reads it
  today breaks. `queueLength` and `queuedEpisodes` unchanged.
- `GET /episodes/processing`: `startedAt` and `stage` come from the
  matching job, so every processing row is populated.
- `GET /settings/whisper/capacity`: `enabled` (the toggle), `backend`,
  `active` (both conditions met) with `inactiveReason` (`disabled` or
  `local_backend`) when not, capacity, max episodes (configured and
  effective), chunk workers (configured and effective), in-flight
  requests, transcribing episodes, and `worstCaseInFlight`
  (`whisperPoolMaxEpisodes * transcribeConcurrentChunks`) with
  `exceedsCapacity: bool`.
- `PATCH /settings`: the three new keys. Changing any of them calls
  `WhisperPool.resize` in the leader through the settings version counter;
  other workers do not hold a pool.
- `openapi.yaml`: the three settings, the capacity endpoint, `jobs` and
  `whisper` on the status schema.

### UI

- Settings > Transcription (`TranscriptionSection.tsx`): under the remote
  backend fields, a toggle "Run episodes in parallel against the remote
  Whisper backend" (off by default) with a one-line explanation, then the
  two dials next to "Concurrent chunks", each with a one-line explanation,
  plus a derived line: "Up to N requests in flight against a capacity of
  C", with `text-warning` when N exceeds C and a note that the pool caps
  it. The dials are disabled while the toggle is off, and the whole block
  is disabled with the reason shown when the backend is local. Follow
  `design-guide/MinusPod Design System.dc.html`.
- Status bar (`GlobalStatusBar.tsx`): the collapsed row shows the oldest
  job as today plus a "+N running" chip when there are more; the expanded
  view lists every job with its own stage, progress and elapsed time.
- Episode page and processing list: no change beyond populated
  `startedAt` and `stage`.

### Backward compatibility

With `whisper_pool_enabled` off (the default) nothing observable changes:
one run at a time, runs start in the process that starts them today, the
chunk executor is sized by `transcribe_concurrent_chunks`, and a Play on an
idle instance starts immediately. The queue and status refactors are
internal and produce the same payloads with one job.

When an operator turns the toggle on with the defaults,
`whisper_pool_max_episodes=1` still means one run at a time, and
`whisper_pool_max_requests=4` matches the existing
`transcribe_concurrent_chunks` default, so the effective chunk workers are
`min(4, 4 // 1) = 4`. The two changes at that point are where a run starts
(leader instead of API worker, with the Play latency described above) and
the 429 handling. An operator who has raised `transcribe_concurrent_chunks`
above 4 sees it capped by the pool until they raise
`whisper_pool_max_requests` to match; the capacity endpoint and the settings
page say so.

### Testing

- `ProcessingQueue`: N slots across threads and across two processes
  (fork), limit refusal, per-slot stale clearing, release by key, shrink
  while occupied.
- `WhisperPool`: in-flight never exceeds capacity when episodes x chunks
  is larger; `chunk_workers` with 1, 2 and 3 transcribing episodes; resize
  up and down under held permits; toggle off or local backend yields an
  inactive pool, a no-op `slot()`, `chunk_workers` equal to
  `transcribe_concurrent_chunks`, and effective episode concurrency 1.
- Inactive pool: `start_background_processing` starts the run in the
  calling process as today, and the dispatcher claims one row at a time.
- Dispatcher: claims up to the limit, respects the rate-limit hold, does
  not claim above a shrunk limit, maintenance runs once per pass, a worker
  exception closes its row and frees its slot.
- API enqueue: each of the six trigger paths results in a pending row with
  the priority it carries today, and the dispatcher starts it.
- Status service: two jobs updated independently; completing one leaves
  the other; `currentJob` is the oldest.
- Token accumulator and run log: two threads accumulate separately.
- Whisper 429: retried, not counted as a failed chunk.
- Regression: with the toggle off a fixture episode produces the same
  transcript, markers, status payloads and status transitions as before,
  and a Play on an idle instance starts in the request's process.

### Out of scope

- An explicit endpoint list with client-side balancing and health checks.
- Parallelism on the local backend.
- A separate LLM concurrency cap. The rate-limit hold pauses every start
  and keeps queue order, which is the agreed handling for provider limits.
