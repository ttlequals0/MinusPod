# Parakeet transcription backend (design)

Historical design. Runtime behavior and cache paths are documented in [Transcription](../transcription.md#parakeet); capacity guidance is in [Installation](../installation.md#parakeet-sizing). The implementation now supports the CPU image on Linux arm64; Apple GPU acceleration remains outside its scope.

Issue #834. Adds NVIDIA Parakeet TDT 0.6B v3 as a third transcription
backend that runs in-process, next to `local` (faster-whisper) and
`openai-api` (remote HTTP). A remote Parakeet server already works through
`openai-api`; this design covers the built-in option.

## Goals

- `WHISPER_BACKEND=parakeet` (and the `whisper_backend` setting) transcribes
  with Parakeet inside the MinusPod container on CUDA or CPU.
- Output has the same segment shape the pipeline consumes today:
  `{start, end, text, words: [{word, start, end}]}`.
- Off unless selected. Existing installs see no behavior change.
- Works as the failover transcriber too (`failover_whisper_backend`).
- No quality regression path is forced on anyone: Whisper stays the default.

## Non-goals

- Replacing faster-whisper or changing its defaults.
- Apple Silicon support inside the container. Mac users keep the remote
  path (whisper.cpp `parakeet-cli` behind an OpenAI-compatible adapter),
  which the issue documents.
- Per-model selection. One model name is supported: `nemo-parakeet-tdt-0.6b-v3`.

## Runtime choice

`onnx-asr` with ONNX Runtime. Reasons, in order:

- `onnxruntime` is already a transitive dependency (faster-whisper's Silero
  VAD), so the CPU image needs only the pure-Python `onnx-asr` package.
- The GPU image already ships the cuDNN 9, cuBLAS, cuFFT and cuRAND pip
  wheels that the ONNX Runtime CUDA provider loads, as torch dependencies.
- NeMo is the official runtime but its `asr` extra is a large dependency
  tree on top of torch, which is a poor fit for an image that is audited
  with `pip-audit --strict`.
- sherpa-onnx CUDA wheels come from a separate index tied to specific CUDA
  and cuDNN builds, which the release flow cannot pin through
  `requirements.txt`.

Known risk: the issue reporter saw Cyrillic tokens inside English speech
through onnx-asr and none through whisper.cpp. Parakeet v3 is multilingual
and has no language forcing, so the mitigation lives in MinusPod (see
"Script filter"). The GPU host test decides whether that is enough.

## Pinned facts about onnx-asr 0.12.0 (read from the wheel, not run)

- `onnx_asr.load_model(name, quantization=None | "int8", providers=[...])`
  resolves `nemo-parakeet-tdt-0.6b-v3` to the Hugging Face repo
  `istupakov/parakeet-tdt-0.6b-v3-onnx` and caches it under the hub cache
  MinusPod already sets (`HUGGINGFACE_HUB_CACHE`).
- `.with_timestamps().recognize(waveform_float32, sample_rate=16000)` returns
  `TimestampedResult(text, timestamps, tokens)`. `timestamps` are token
  start times at `window_step * subsampling_factor` resolution (0.08 s for
  this model). There are no token end times.
- SentencePiece tokens carry a leading `U+2581` on word starts.
- `onnxruntime.preload_dlls()` (1.21+) loads the CUDA and cuDNN shared
  libraries from the nvidia pip wheels before a session is created.
- The package depends on numpy >= 1.22.4 only; `onnxruntime` is an extra, so
  it does not fight the existing `onnxruntime==1.24.4` pin or `numpy<2.0`.

## Architecture

### Backend constant and classification

`src/config.py` adds `WHISPER_BACKEND_PARAKEET = 'parakeet'` and
`WHISPER_LOCAL_BACKENDS = (WHISPER_BACKEND_LOCAL, WHISPER_BACKEND_PARAKEET)`.

Every binary branch today is `== WHISPER_BACKEND_API` with everything else
falling into the faster-whisper path. Those stay. Parakeet takes the
in-process path (GPU admission, decode activity counter, idle unload, local
health, local failover probe) and branches on `== WHISPER_BACKEND_PARAKEET`
only where faster-whisper objects or model names are touched:

| Site | Change |
|---|---|
| `_local_unavailable_message`, `local_transcription_available`, `_require_local_transcription` | Take the backend; parakeet checks `onnx_asr` import, whisper checks faster-whisper |
| `Transcriber.transcribe` | After admission and preprocessing, `if backend == PARAKEET: return ParakeetDecoder.decode(path, language, ...)`; the faster-whisper body is unchanged |
| `_transcribe_sequential` | Same split; Parakeet has no VAD so it is the same decode |
| `probe_local_transcription` | Parakeet probe decodes the 1 s probe WAV through the same decoder |
| `get_local_transcriber_health`, `_record_local_transcription_outcome`, `_record_local_stats` | Carry the active backend label instead of the hard-coded `local` |
| `WhisperModelSingleton.get_configured_model` callers in chunk sizing | Parakeet uses a fixed memory profile (see Chunking) |
| `unload_whisper_if_idle`, `unload_after_repair`, `_handle_processing_failure`, settings reload hooks | Also unload or mark the Parakeet singleton |
| `failover.is_configured`, `_configured_in_transaction`, probe runner (`backend == LOCAL`), `_local_runtime_config`, outcome persistence | Treat parakeet as local-like; its runtime config is device only |
| `api/system._effective_whisper_config` | Parakeet reports model `parakeet-tdt-0.6b-v3` and the resolved device |
| `whisper_pool._is_active` | Unchanged: pool is API-only |

### New module `src/parakeet_transcriber.py`

Keeps transcriber.py from growing further. Contents:

- `ParakeetModelSingleton`: `get_instance()`, `unload_model()`,
  `is_loaded()`, `mark_for_reload()`. Loads once per process with
  `providers=['CUDAExecutionProvider', 'CPUExecutionProvider']` when
  `resolve_whisper_device() == 'cuda'` (after `onnxruntime.preload_dlls()`),
  else CPU only. Quantization: `int8` on CPU, none on CUDA. Load failures
  raise `ModelLoadError` so the failover trigger and the existing error
  paths apply unchanged.
- `decode(wav_path, language) -> list[dict]`: reads the preprocessed
  16 kHz mono WAV with `wave` + numpy, windows it, recognizes each window,
  assembles words, applies the script filter, groups words into segments.

### Windowing

The encoder attends over the whole input, so long inputs cost memory and
the reporter measured dropped speech above 120 s. Constants:

| Name | Value | Source |
|---|---|---|
| `PARAKEET_WINDOW_S` | 30.0 | Issue #834 test: no drops at 30 s |
| `PARAKEET_OVERLAP_S` | 2.0 | Issue #834 |

A word is kept from the window whose core (window minus half the overlap
at each inner edge) contains the word's midpoint. Windows are consecutive
`float32` slices of the one in-memory array; no temp files.

The outer pipeline keeps its existing chunking: `calculate_optimal_chunk_duration`
is given a fixed Parakeet profile so chunks are the default size regardless
of GPU memory, since the decoder bounds memory per 30 s window itself.

### Words and timestamps

- A token starting with `U+2581` starts a word; others append to the
  current word.
- Word `start` is its first token's timestamp. Word `end` is the next
  word's first token timestamp, capped at `start + 1.5 s`; the last word
  in a window ends at its last token's timestamp plus 0.08 s. This is
  documented as an approximation in the module.
- No `probability` key: the pipeline does not read one.

### Segments

Words are grouped into segments, mirroring the shape faster-whisper gives
the matchers: a new segment starts after sentence punctuation (`.`, `?`,
`!`), after a pause longer than 1.0 s, or when the segment would exceed
25 s. Segment `text` is the joined words, stripped.

### Script filter

When the effective transcription language is English (the default), a
word whose letters are mostly outside the Latin script (Cyrillic, Greek,
CJK, Arabic and so on) is dropped before segmenting, and the count is
logged once per decode at INFO. With language `auto` or a non-English
setting nothing is dropped. This runs only in the Parakeet decoder;
`filter_hallucinations` stays backend-neutral.

`is_foreign_language` detection still runs: the decoder reports
`detected_language` as the configured language so `_should_detect_foreign_language`
behaves as it does for pinned-English Whisper.

### Settings and validation

- Registry (`src/database/settings.py`): `whisper_backend` gains
  `validator=_one_of(LOCAL, API, PARAKEET)` (it has none today);
  `failover_whisper_backend` extends its `_one_of`.
- `src/api/settings.py`: both backend validations (pre-check and apply)
  accept the three values. Saving a backend change marks both singletons
  for reload.
- `config_transfer`: no change; both keys are already portable and now
  validated on import.
- openapi.yaml: every `[local, openai-api]` enum gains `parakeet` with a
  one-line note.

### Frontend

- `WhisperBackend` type and `WHISPER_BACKENDS` gain `PARAKEET`.
- Transcription section: third option "Local (Parakeet TDT 0.6B v3)".
  Model and compute-type selects show only for `local`; API fields only
  for `openai-api`. A one-line helper under the select for Parakeet: "Runs
  in this container on the configured device. The model downloads into the
  data directory on first use. English transcripts drop words in other
  scripts." No size is stated until it is measured on the host.
- Failover section: third option; the local model input shows only for
  `local`, API fields only for `openai-api`.
- System status: transcriber backend union gains `parakeet`.

### Docker and dependencies

- `requirements.in`: add `onnx-asr` (no extras). Regenerate
  `requirements.txt` with the documented pip-compile command.
- GPU `Dockerfile`: after the requirements step, replace the CPU runtime
  with the CUDA build: `pip uninstall -y onnxruntime && pip install
  onnxruntime-gpu==1.24.4` (same version as the CPU pin; 1.24.1 is
  excluded by onnx-asr). Verified from the wheel's
  `libonnxruntime_providers_cuda.so` NEEDED entries: 1.24.4 links
  `libcudart.so.12`, `libcudnn.so.9`, `libcublas.so.12`, `libcufft.so.11`
  and `libcurand.so.10`, all shipped by the torch cu129 nvidia wheels. A
  CUDA 13 build would not run on older supported host drivers.
- `LD_LIBRARY_PATH` gains the `nvidia/cufft/lib` and `nvidia/curand/lib`
  venv dirs as a fallback to `preload_dlls()`.
- `Dockerfile.cpu`: only the requirements change (CPU provider is already
  there).
- `scripts/check_image_runtime.py` module list gains `onnx_asr`.
- `pip-audit --strict` and the CPU workflow's runtime check are the gates.

### Docs and changelog

- `docs/transcription.md`: new section "Parakeet (built in)" with the
  env line, first-run download, CUDA and CPU notes, the English-only script
  filter, and the known weakness on proper names. A short pointer to the
  remote whisper.cpp path for Apple Silicon (issue #834).
- `docs/environment-variables.md`, `docs/failover.md`, README feature
  line, `.env.example` comment.
- CHANGELOG 2.100.0 Added entry crediting the issue.

## Testing

Unit tests run with `onnx_asr` absent or stubbed; nothing loads the model
on a developer machine.

- `tests/unit/test_parakeet_transcriber.py`: window plan (count, cores,
  last window), word assembly from tokens with timestamps (word-start
  marker, end-time rule, cap), script filter (Cyrillic word dropped under
  `en`, kept under `auto`), segment grouping (punctuation, pause, 25 s
  cap), decode assembling a fake model's results across two overlapping
  windows into correctly offset words.
- Backend selection: settings validation accepts `parakeet` for both
  keys; `get_transcriber_health` and `_effective_whisper_config` report
  the Parakeet label; `failover.is_configured` for a parakeet failover
  depends on availability, not a base URL; `Transcriber.transcribe` routes
  to the decoder when the backend is parakeet (decoder patched).
- Frontend: TranscriptionSection and FailoverSection render the third
  option and hide the Whisper-only fields; Settings applier tests cover
  the new value.
- Existing suites stay green.

## Rollout and the GPU host test

1. Build 2.100.0 GPU image locally, scan, push; dispatch the CPU workflow.
2. Deploy through the normal stack. Nothing changes until the backend is
   switched.
3. In Settings, switch the transcription backend to Parakeet and reprocess
   one or two recent episodes. Check: model download succeeded into the
   data cache, CUDA provider active (log line at load), decode time per
   chunk, non-Latin words dropped count, gap repair counts, resulting ad
   markers against the previous run of the same episodes.
4. Switch back to Whisper and reprocess one episode to confirm the
   onnxruntime swap did not break Silero VAD.
5. Record the measurements in the PR body (no feed or episode identifiers).

## Open questions

- Model download size and first-load time on the host: measured in step 3
  and written into the docs.
- Whether int8 on CUDA is worth offering: not in this change.
