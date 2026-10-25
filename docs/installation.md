# Installation

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [Requirements](#requirements)
- [Parakeet sizing](#parakeet-sizing)
- [Checking resource use](#checking-resource-use)
- [Quick Start](#quick-start)
- [CPU-only image (no GPU)](#cpu-only-image-no-gpu)
- [Intel hybrid CPU tuning (optional)](#intel-hybrid-cpu-tuning-optional)

## Requirements

### Minimum

- Any Docker host (amd64 or arm64), 2 CPU cores, 8 GB RAM
- Anthropic API key, [OpenRouter](https://openrouter.ai) API key, [Ollama](https://ollama.com) for local inference, **or** any OpenAI-compatible endpoint

A GPU is recommended but not required. The CPU image and [remote backends](transcription.md) work without one.

### Memory Requirements

**faster-whisper GPU VRAM:** These approximate values come from the application's [memory profiles](../src/config.py). They are planning values, not measured peaks for every GPU or compute type. They do not apply to Parakeet.

| faster-whisper model | VRAM Required |
|---------------|---------------|
| tiny | ~1 GB |
| base | ~1 GB |
| small | ~2 GB |
| medium | ~4 GB |
| large-v3 | ~5-6 GB |
| turbo | ~5 GB |

**System RAM:** Existing whole-application planning allowances follow. These are not backend-specific benchmark results; audio processing and other services also need memory.

| Episode Length | RAM Required |
|----------------|-------------|
| < 1 hour | 8 GB |
| 1-2 hours | 8 GB |
| 2-4 hours | 12 GB |
| > 4 hours | 16 GB |

### Parakeet sizing

Parakeet uses one fixed model, `nemo-parakeet-tdt-0.6b-v3`. There is no `tiny`/`small`/`large` selector. MinusPod uses int8 weights on CPU and unquantized weights on CUDA; `WHISPER_COMPUTE_TYPE` does not change either choice.

| Deployment | Host RAM | GPU VRAM | Initial model download |
| --- | --- | --- | --- |
| CPU image, amd64 or arm64 | Start with the application's 8 GB minimum; measure the workload before imposing a container limit | None | About 0.670 GB (670,480,039 bytes) |
| GPU image, amd64 with NVIDIA runtime | Same 8 GB application baseline; GPU memory is additional | Peak requirement is not measured in this repository | About 2.550 GB (2,549,805,955 bytes) |

Download sizes total the files selected by [MinusPod's loader](../src/parakeet_transcriber.py), including vocabulary and configuration. They come from the [ONNX model file listing](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/tree/8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce), checked on 2026-10-16. GB here means 1,000,000,000 bytes.

These downloads are disk requirements, not RAM or VRAM minima. Runtime tensors, CUDA workspaces, audio buffers, and concurrent services need additional memory. Measure the ONNX Runtime peak ([Checking resource use](#checking-resource-use)) before choosing a GPU or setting a memory limit.

The cache lives at `$HF_HOME/parakeet/nemo-parakeet-tdt-0.6b-v3`, normally `/app/data/.cache/parakeet/nemo-parakeet-tdt-0.6b-v3`. Keep it on the persistent data volume. Switching between CPU and CUDA downloads another weight set; retaining both uses about 3.220 GB before download metadata or temporary files. Allow additional disk space for those files, episode audio, and backups.

Inference uses 30-second windows with 2-second overlap. This bounds each model call, not the entire application's RAM use: the decoder also holds its input WAV and transcript data. See [Parakeet setup](transcription.md#parakeet) for device settings, offline use, and limitations.

### Checking resource use

Process a representative retained episode, including your longest usual episode. Watch from cold model load through transcription, detection, and audio cutting:

```bash
docker stats minuspod

# NVIDIA hosts: sample device-wide VRAM use during the run.
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv -l 1
```

Record the image version, CPU/GPU, backend, episode duration, highest observed container RAM, and highest observed GPU memory. GPU totals include other processes. A single idle reading is not a peak measurement. Leave capacity for the host and other services; a local Ollama model needs its own RAM/VRAM budget.

## Quick Start

Before processing an episode, set `BASE_URL`, configure provider access (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, or `OPENAI_BASE_URL` for a local endpoint), and choose a model. `OPENAI_MODEL` seeds the model settings, or you can choose each model under Settings > AI & Processing > AI Models. Compose starts without `MINUSPOD_MASTER_PASSPHRASE`. Set it to encrypt provider keys saved through the UI and API-downloaded database backups. Env vars seed unset settings; stored values win afterward.

```bash
# 1. Create environment file
cat > .env << EOF
ANTHROPIC_API_KEY=your-key-here
BASE_URL=http://localhost:8000
MINUSPOD_MASTER_PASSPHRASE=long-random-string-you-will-not-lose
OPENAI_MODEL=claude-haiku-4-5
EOF

# 2. Create data directory
mkdir -p data

# 3. Run (pick one)
docker compose up -d                            # NVIDIA GPU image
docker compose -f docker-compose.cpu.yml up -d  # CPU image
```

Access the web UI at `http://localhost:8000/ui/` to add and manage feeds.

Compose refuses protected API access until you set an application password under Settings > Security. Remote setup requires `MINUSPOD_SETUP_TOKEN` in `.env` and the same value in the `X-MinusPod-Setup-Token` header (loopback needs no token):

```bash
curl -sS -X PUT http://your-server:8000/api/v1/auth/password \
  -H 'Content-Type: application/json' \
  -H 'X-MinusPod-Setup-Token: one-time-setup-token' \
  --data '{"newPassword":"a-long-application-password"}'
```

Remove the token after setup. For why Compose requires a password and how `MINUSPOD_REQUIRE_AUTH` works, see [Security recommendations](security-and-storage.md#security-recommendations) and [Environment Variables](environment-variables.md#security).

`MINUSPOD_MASTER_PASSPHRASE` is strongly recommended for production. Without it, provider API keys go into the database as plaintext. Setting it later migrates existing plaintext rows to `enc:v1:` encrypted storage on the next boot, with a mandatory pre-migration SQLite snapshot in `data/backups/`. Restoring an encrypted API download requires the same passphrase that created it, so pick a long random value and keep it somewhere separate from the database.

### CPU-only image (no GPU)

No NVIDIA GPU? Pull the CPU variant. It drops the bundled NVIDIA/CUDA Python wheels; the image is around 3 GB instead of ~12 GB. The CPU image is published as a multi-arch manifest covering `linux/amd64` and `linux/arm64`, so Docker on the puller's machine picks the right architecture automatically. The GPU image stays amd64-only.

Reuse the same `.env` and `data/` directory as the Quick Start, then:

```bash
docker compose -f docker-compose.cpu.yml up -d
```

That pulls `ttlequals0/minuspod:cpu` (the floating CPU tag). To pin a release, set `MINUSPOD_VERSION=X.Y.Z-cpu` in `.env`. The `:latest` tag is always the GPU image; CPU users track `:cpu` or a versioned `-cpu` tag. For vetted builds, set `MINUSPOD_VERSION=stable-cpu` (or `stable` for GPU): these tags move only after a production soak (see [Releasing & Channels](releasing.md)).

The CPU image transcribes locally with `faster-whisper` or Parakeet. For Parakeet, set `WHISPER_BACKEND=parakeet`; keep `WHISPER_DEVICE=cpu`. A remote backend (`WHISPER_BACKEND=openai-api`) also works: Groq, OpenAI, or a self-hosted whisper.cpp server (see `docker-compose.whisper.yml`). See [Remote Whisper APIs](transcription.md#remote-whisper-apis) for the `.env` settings.

### Intel hybrid CPU tuning (optional)

Modern Intel CPUs (12th gen and newer) split their cores into fast P-cores and slow E-cores. The thread pool behind `faster-whisper` is not aware of that split, so on a hybrid chip the work can land on the slow E-cores and thrash the shared cache. Capping the thread count and keeping the work on the P-cores can make a big difference: one user reported a 30-minute episode dropping from roughly 20 minutes to under a minute on an i7-13620H. More threads is not automatically faster, so match the count to your hardware rather than maxing it.

Two knobs, smallest change first:

1. Cap the OpenMP thread pool to your P-core count. This alone removes the oversubscription:

   ```bash
   docker run -e OMP_NUM_THREADS=8 ... ttlequals0/minuspod:cpu
   ```

2. Pin the container to the P-cores so the scheduler cannot push work onto E-cores. Find the P-core ids with `lscpu --all --extended` (the higher-clocked cores), then:

   ```bash
   # Docker
   docker run -e OMP_NUM_THREADS=8 --cpuset-cpus=0-11 ... ttlequals0/minuspod:cpu
   ```

   ```ini
   # Podman Quadlet (minuspod.container)
   [Container]
   Environment=OMP_NUM_THREADS=8
   CPUSetCPUs=0-11
   ```

   If you would rather bind threads than restrict the cgroup, `OMP_PROC_BIND=close` with `OMP_PLACES=cores` does the same job.

Match `OMP_NUM_THREADS` and the cpuset range to your own chip; the values above are for a 6 P-core part with 12 P-threads. The payoff depends on the CPU and model size. For sustained transcription, a remote Whisper API is still the better answer.

<details>
<summary>Build the CPU image locally</summary>

If you are modifying `Dockerfile.cpu` or want to compile from source, uncomment the `build:` block in `docker-compose.cpu.yml` and run with `--build`:

```bash
docker compose -f docker-compose.cpu.yml up -d --build
```

</details>

---

[< Docs index](README.md) | [Project README](../README.md)
