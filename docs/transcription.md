# Transcription

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [Choosing between Whisper and Parakeet](#choosing-between-whisper-and-parakeet)
- [Whisper (faster-whisper)](#whisper-faster-whisper)
  - [GPU compute type](#gpu-compute-type)
- [Parakeet](#parakeet)
- [Remote Whisper APIs](#remote-whisper-apis)
  - [whisper.cpp with Docker (NVIDIA GPU)](#whispercpp-with-docker-nvidia-gpu)
  - [whisper.cpp on Apple Silicon (native)](#whispercpp-on-apple-silicon-native)
  - [Intel GPU (OpenVINO Model Server)](#intel-gpu-openvino-model-server)
  - [Testing a remote endpoint](#testing-a-remote-endpoint)
  - [Groq](#groq)
  - [OpenAI Whisper API](#openai-whisper-api)
- [Chunked transcription](#chunked-transcription)
- [Failover transcriber](#failover-transcriber)
- [Transcription language](#transcription-language)
- [Processing timeouts](#processing-timeouts)

MinusPod has three transcription backends. Pick one with `WHISPER_BACKEND` or in Settings > Transcription > Backend. The setting names still say "whisper" for all of them.

| Backend | `WHISPER_BACKEND` | Runs where | Good at | Settings that apply |
|---|---|---|---|---|
| faster-whisper (default) | `local` | In the container: CUDA in the GPU image, CPU in the CPU image | Proper names, sponsor names, promo codes; several model sizes | `WHISPER_MODEL`, `WHISPER_COMPUTE_TYPE`, `WHISPER_DEVICE`, language |
| Parakeet TDT 0.6B v3 | `parakeet` | In the container through onnx-asr: CUDA in the GPU image, CPU in the CPU image | Speed on a GPU; fewer repetition loops | `WHISPER_DEVICE`, language |
| Remote API | `openai-api` | A separate server (whisper.cpp, Groq, OpenAI, OpenVINO) | Hosts with no GPU; offloading to another machine | `WHISPER_API_BASE_URL`, `WHISPER_API_KEY`, `WHISPER_API_MODEL`, `whisperMaxAttempts` |

Every backend runs without a GPU.

A local model, from either local backend, stays loaded while the queue has work and unloads 300 seconds after the queue empties.

## Choosing between Whisper and Parakeet

Measured on a Tesla T4 across three episodes:

- Speed: Parakeet transcribed 1.8x to 2.5x faster than Whisper large-v3.
- Ad detection: results matched. Every ad found with both transcripts started and ended within 3 seconds of each other.
- Names: Parakeet is weaker on sponsor names and promo codes, so transcript normalization rules (Settings > AI & Processing > Transcript Normalization) matter more with it.
- Fillers: Parakeet keeps "uh" and "um" in the transcript.

Based on those numbers, Parakeet suits a GPU host that wants shorter processing time, and faster-whisper suits shows where sponsor names and codes drive detection. Use the remote API backend when the host has no usable GPU.

## Whisper (faster-whisper)

The default local backend. It runs faster-whisper on CTranslate2, with the model set by `WHISPER_MODEL` (default `small` on GPU, `tiny` in the CPU image). `WHISPER_MODEL` and `WHISPER_COMPUTE_TYPE` apply to this backend only.

### GPU compute type

faster-whisper runs on CTranslate2, which only supports certain compute types per GPU generation. MinusPod exposes `WHISPER_COMPUTE_TYPE` as an env var and as a dropdown in Settings > Transcription. The default `auto` picks `float16` on CUDA and `int8` on CPU, matching the prior hardcoded behavior. If `float16` fails at model init (common on Pascal GTX 10xx and Maxwell GTX 9xx, which cannot do fp16 math in CTranslate2), the server retries `int8_float16`, then `int8`, then `float32` and logs the final active type. Any other explicit choice that fails is raised instead of silently masked.

Pick a value that matches your GPU:

| GPU generation | Cards | Recommended value |
|---|---|---|
| Blackwell, Hopper, Ada, Ampere | RTX 50xx, H100/H200, RTX 40xx / L40, RTX 30xx / A100 | `auto` (float16) |
| Turing | RTX 20xx, GTX 16xx, T4 | `auto` (float16) |
| Volta | V100, Titan V | `auto` (float16) |
| Pascal (consumer) | GTX 1060 / 1070 / 1080 / 1080 Ti, Titan Xp | `int8` |
| Pascal P100, Jetson TX2 | P100, Jetson TX2 | `float32` |
| Maxwell | GTX 9xx, Titan X Maxwell | `float32` |

Sources:

- CTranslate2 compute-type support matrix: [opennmt.net/CTranslate2/quantization.html](https://opennmt.net/CTranslate2/quantization.html)
- Official NVIDIA CUDA compute-capability table: [developer.nvidia.com/cuda-gpus](https://developer.nvidia.com/cuda-gpus)

## Parakeet

NVIDIA Parakeet TDT 0.6B v3 is the second local transcription backend. It runs through ONNX Runtime inside the MinusPod container: on CUDA in the GPU image and on CPU in the CPU image. No separate server is needed.

Select it in Settings > Transcription > Backend with "Local (Parakeet TDT 0.6B v3)", or set:

```bash
WHISPER_BACKEND=parakeet
```

Use `WHISPER_DEVICE=cpu` with the CPU image or `WHISPER_DEVICE=cuda` with the NVIDIA image. The shipped Compose files pass these values from `.env`; existing saved settings take precedence over environment defaults.

The first use downloads weights into `$HF_HOME/parakeet/nemo-parakeet-tdt-0.6b-v3`, normally `/app/data/.cache/parakeet/nemo-parakeet-tdt-0.6b-v3`. CPU uses int8 weights; CUDA uses the unquantized export. Download sizes, RAM guidance, and the resource-measurement procedure are in [Installation > Parakeet sizing](installation.md#parakeet-sizing).

The decoder uses 30-second windows with 2-second overlap. Each model call is bounded, but the input WAV and transcript data still consume system RAM. Longer inputs and concurrent processing can increase total memory use.

For an offline installation, first complete a transcription on a connected instance using the same device mode. Preserve the entire model directory, including external weights and vocabulary. A complete local copy lets the loader continue when a later download attempt fails; an incomplete cache cannot. Keep the directory writable for normal model updates.

Parakeet v3 is multilingual and cannot be pinned to a language. With the transcription language set to `en`, words written in other scripts (Cyrillic, Greek, CJK, Arabic and so on) are dropped before the transcript is built, and the count is logged at INFO. With `auto` or a non-English language, nothing is dropped.

The faster-whisper model, compute type and the API settings do not apply to this backend. Parakeet can also be the failover transcriber: choose it under Settings > AI & Processing > Failover, or set `failoverWhisperBackend` to `parakeet`. See [Failover](failover.md#configuration-reference).

In tests on narrative shows with music beds (issue #834), Parakeet ran faster than the Whisper large models and produced fewer repetition loops. See [Choosing between Whisper and Parakeet](#choosing-between-whisper-and-parakeet) for the measured trade-offs.

The CPU image supports Linux arm64, including Docker on Apple Silicon. This uses the CPU provider; MinusPod does not select Metal or CoreML acceleration. Native macOS and Mac GPU performance have not been measured. For an accelerated external transcriber, use an OpenAI-compatible server with `WHISPER_BACKEND=openai-api`.

## Remote Whisper APIs

The `openai-api` backend sends audio to any OpenAI-compatible transcription endpoint. The sections below cover common servers.

### whisper.cpp with Docker (NVIDIA GPU)

A ready-to-use compose file is provided at [`docker-compose.whisper.yml`](../docker-compose.whisper.yml). It runs [whisper.cpp](https://github.com/ggml-org/whisper.cpp) as a standalone GPU-accelerated transcription server.

**1. Download the model:**

```bash
git clone --depth 1 https://github.com/ggml-org/whisper.cpp
bash whisper.cpp/models/download-ggml-model.sh large-v3-turbo
mkdir -p models && mv whisper.cpp/models/ggml-large-v3-turbo.bin models/
```

Other models are available: replace `large-v3-turbo` with `tiny`, `base`, `small`, `medium`, or `large-v3`. See the [whisper.cpp models README](https://github.com/ggml-org/whisper.cpp/tree/master/models) for the full list.

**2. Start the server:**

```bash
docker compose -f docker-compose.whisper.yml up -d
```

**3. Configure MinusPod** (`.env` or `docker-compose.yml`):

```bash
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=http://whisper-server:8765/v1
WHISPER_DEVICE=cpu
```

If MinusPod and whisper-server are on the same Docker network, use the container name (`whisper-server`). If they are on separate hosts, use the host IP and the exposed port (`http://your-server:8765/v1`).

The `--dtw large.v3.turbo` flag enables word-level timestamps for precise ad boundary detection. On CUDA GPUs, `--no-flash-attn` is required alongside `--dtw`. Flash attention silently disables DTW, causing word-level timestamps to be missing from the API response. On Apple Silicon (Metal), this flag is not needed. `WHISPER_DEVICE=cpu` prevents MinusPod from attempting to initialize a local CUDA GPU. MinusPod already preprocesses audio to 16kHz mono WAV before sending it to the API, so the whisper.cpp `--convert` flag is not needed.

> **Warning:** If you add `--convert` for use with other clients, be aware that whisper.cpp writes temporary converted files to the current working directory. In Docker, the default CWD may not be writable, causing whisper.cpp to silently return empty transcription results (200 with 0 segments). Set `working_dir: /tmp` in your compose file or mount a writable volume if you need `--convert`.

### whisper.cpp on Apple Silicon (native)

whisper.cpp runs natively on Apple Silicon with Metal acceleration. Build from source or use Homebrew:

```bash
# Download model
git clone --depth 1 https://github.com/ggml-org/whisper.cpp
bash whisper.cpp/models/download-ggml-model.sh large-v3-turbo

# Build and run the server
cd whisper.cpp && make -j
./build/bin/whisper-server \
  --host 0.0.0.0 --port 8765 \
  --model models/ggml-large-v3-turbo.bin \
  --inference-path /v1/audio/transcriptions \
  --dtw large.v3.turbo

# Configure MinusPod
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=http://host.docker.internal:8765/v1
WHISPER_DEVICE=cpu
```

> **Linux users:** Replace `host.docker.internal` with your host IP, or add `extra_hosts: ["host.docker.internal:host-gateway"]` to your Docker service definition.

### Intel GPU (OpenVINO Model Server)

On an Intel host with a capable integrated or discrete GPU, you can offload transcription to the GPU instead of the CPU. OpenVINO Model Server runs Whisper as a remote OpenAI-compatible backend with word-level timestamps, so the CPU image's transcription stops pinning every core. See the dedicated [Intel GPU Transcription (OpenVINO)](transcription-openvino.md) guide for the full setup.

### Testing a remote endpoint

Getting the base URL path right is the fiddly part of a remote backend: most servers answer something on every path, but only one path accepts transcription requests. The **Test connection** button under the API Base URL field (Settings > Transcription, with the backend set to Remote API) uploads a one-second generated audio sample using the same request the real pipeline sends, so a passing test means an actual episode upload will work. The values currently in the form are used, saved or not, so you can test a URL before committing it.

The result separates three situations:

- Could not connect: nothing answered at that host and port. Check the address, the port, and that the server is running.
- Reachable, but the request failed: something is listening, but either there is no transcription endpoint at that path (HTTP 404; OVMS only answers under its versioned base such as `/v3`), or the endpoint refused the request (wrong model name, missing API key). The message includes the server's response where it helps. A slow answer counts here too: if the connection succeeds but the server takes more than 30 seconds (common while a model cold-loads), the test says so rather than reporting the server as down.
- Connected: the endpoint accepted the sample and returned a transcription result.

The sample is uploaded in the same format a real episode would use: FLAC by default, or WAV when Skip FLAC compression is on. That means the test also catches a server that cannot decode FLAC before a full episode fails on it.

A saved whisper API key is sent with the probe only when the URL being tested points at the same server as the saved base URL. The key is never sent to a URL you have not saved, so save both the key and the base URL before testing a keyed endpoint.

### Groq

[Groq](https://groq.com) offers fast cloud-based whisper transcription:

```bash
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=https://api.groq.com/openai/v1
WHISPER_API_KEY=gsk_your_key_here
WHISPER_API_MODEL=whisper-large-v3-turbo
WHISPER_DEVICE=cpu
```

### OpenAI Whisper API

```bash
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=https://api.openai.com/v1
WHISPER_API_KEY=sk-your_key_here
WHISPER_API_MODEL=whisper-1
WHISPER_DEVICE=cpu
```

All settings can also be configured via the Settings UI under the Transcription section.

## Chunked transcription

Episodes longer than one chunk are transcribed in pieces. Local faster-whisper sizes chunks from available GPU memory; the log line "Calculated chunk duration" shows the result. Parakeet starts with 1,800-second outer chunks and decodes each through its 30-second windows. Outer chunks overlap by 30 seconds. Remote API chunk sizes come from their separate settings.

Each chunk is cut from the source file by its own ffmpeg pass, which also
applies loudness normalization and band-pass filtering in the same step. Those
passes are CPU-bound and slower than GPU inference, so they run ahead of the
GPU: two extraction workers prepare upcoming chunks while the current one
transcribes. If a chunk hits GPU out-of-memory or an extraction timeout, the
chunk size is halved and the queued extractions are redone at the new size.

The remote API backend parallelizes differently: with no shared GPU to
protect, chunks upload and transcribe concurrently.

## Failover transcriber

An optional standby transcriber (Settings > AI & Processing > Failover) takes over when the active backend is unreachable, times out, or an API backend rejects the request with 401, 402, 403, or 404. On the API backend, `whisperMaxAttempts` (Settings > Transcription, 1-10, default 2) replaces the old fixed retry count before a chunk counts as failed and the switch is considered. See [Failover](failover.md#what-happens-mid-run) for how a switch affects a chunk already in progress, and [Failover > Configuration reference](failover.md#configuration-reference) for every failover transcriber setting.

## Transcription language

faster-whisper and remote transcription requests default to English. Parakeet detects language itself; its `en` setting applies the script filter described [above](#parakeet). If you run a non-English show, pick the language in Settings > Transcription or set `WHISPER_LANGUAGE` on first boot. Use `auto` for multilingual feeds; the model detects the language per request. Full list: [supported languages](https://whisper-api.com/docs/languages/).

## Processing timeouts

Two knobs for long-running jobs, both in the same panel:

- Soft timeout (default 60 minutes): when stale display entries can clear after SQLite confirms no live run owns them. A live owner is warned about, not evicted.
- Hard timeout (default 120 minutes): how long the automatic queue waiter follows a started run before returning its queue row to pending for a later check. It does not let another worker publish over a live owner. Must be greater than the soft timeout.

Long CPU runs can reach these limits. The log names the setting to raise. Values live in the database and take effect immediately; `PROCESSING_SOFT_TIMEOUT` and `PROCESSING_HARD_TIMEOUT` only seed fresh installs.

---

[< Docs index](README.md) | [Project README](../README.md)
