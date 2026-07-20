# Whisper / Transcription

[< Docs index](README.md) | [Project README](../README.md)

---

By default, MinusPod uses faster-whisper with a local NVIDIA GPU for transcription. If you don't have an NVIDIA GPU (e.g. Apple Silicon Mac), you can use any OpenAI-compatible whisper API as the transcription backend.

## GPU Compute Type

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

## whisper.cpp with Docker (NVIDIA GPU)

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

## whisper.cpp on Apple Silicon (native)

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

## Intel GPU (OpenVINO Model Server)

On an Intel host with a capable integrated or discrete GPU, you can offload transcription to the GPU instead of the CPU. OpenVINO Model Server runs Whisper as a remote OpenAI-compatible backend with word-level timestamps, so the CPU image's transcription stops pinning every core. See the dedicated [Intel GPU Transcription (OpenVINO)](transcription-openvino.md) guide for the full setup.

## Testing a remote endpoint

Getting the base URL path right is the fiddly part of a remote backend: most servers answer something on every path, but only one path accepts transcription requests. The **Test connection** button under the API Base URL field (Settings > Transcription, with the backend set to Remote API) uploads a one-second generated audio sample using the same request the real pipeline sends, so a passing test means an actual episode upload will work. The values currently in the form are used, saved or not, so you can test a URL before committing it.

The result separates three situations:

- Could not connect: nothing answered at that host and port. Check the address, the port, and that the server is running.
- Reachable, but the request failed: something is listening, but either there is no transcription endpoint at that path (HTTP 404; OVMS only answers under its versioned base such as `/v3`), or the endpoint refused the request (wrong model name, missing API key). The message includes the server's response where it helps. A slow answer counts here too: if the connection succeeds but the server takes more than 30 seconds (common while a model cold-loads), the test says so rather than reporting the server as down.
- Connected: the endpoint accepted the sample and returned a transcription result.

The sample is uploaded in the same format a real episode would use: FLAC by default, or WAV when Skip FLAC compression is on. That means the test also catches a server that cannot decode FLAC before a full episode fails on it.

A saved whisper API key is sent with the probe only when the URL being tested points at the same server as the saved base URL. The key is never sent to a URL you have not saved, so save both the key and the base URL before testing a keyed endpoint.

## Groq

[Groq](https://groq.com) offers fast cloud-based whisper transcription:

```bash
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=https://api.groq.com/openai/v1
WHISPER_API_KEY=gsk_your_key_here
WHISPER_API_MODEL=whisper-large-v3-turbo
WHISPER_DEVICE=cpu
```

## OpenAI Whisper API

```bash
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=https://api.openai.com/v1
WHISPER_API_KEY=sk-your_key_here
WHISPER_API_MODEL=whisper-1
WHISPER_DEVICE=cpu
```

All settings can also be configured via the Settings UI under the Transcription section.

## Transcription language

Whisper is pinned to English by default. That keeps it from misdetecting on music intros or cold opens (a common failure mode on podcasts). If you run a non-English show, pick the language in Settings > Transcription or set `WHISPER_LANGUAGE` on first boot. Use `auto` for multilingual feeds; Whisper will detect per request. Full list: [supported languages](https://whisper-api.com/docs/languages/).

## Processing timeouts

Two knobs for long-running jobs, both in the same panel:

- Soft timeout (default 60 min): how long a job can sit in the queue before it's treated as stuck and cleared. Jobs killed by a worker restart are cleared in seconds regardless, via the queue's flock probe.
- Hard timeout (default 120 min): how long before the processing lock is force-released even when a worker still holds it. Backstop for a hung ffmpeg or runaway Whisper call. Must be greater than the soft timeout.

Three-hour CPU runs with the largest Whisper model hit these. When they fire, the log line names the setting to raise. Values live in the DB and take effect immediately; `PROCESSING_SOFT_TIMEOUT` and `PROCESSING_HARD_TIMEOUT` only seed fresh installs.

---

[< Docs index](README.md) | [Project README](../README.md)
