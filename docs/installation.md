# Installation

[< Docs index](README.md) | [Project README](../README.md)

---

## Requirements

- Docker with NVIDIA GPU support (for local Whisper), **or** a [remote Whisper backend](transcription.md) (no GPU needed)
- Anthropic API key, [OpenRouter](https://openrouter.ai) API key, [Ollama](https://ollama.com) for local inference, **or** any OpenAI-compatible endpoint

### Memory Requirements

**GPU VRAM:**

| Whisper Model | VRAM Required |
|---------------|---------------|
| tiny | ~1 GB |
| base | ~1 GB |
| small | ~2 GB |
| medium | ~4 GB |
| large-v3 | ~5-6 GB |
| turbo | ~5 GB |

**System RAM:**

| Episode Length | RAM Required |
|----------------|-------------|
| < 1 hour | 8 GB |
| 1-2 hours | 8 GB |
| 2-4 hours | 12 GB |
| > 4 hours | 16 GB |

## Quick Start

```bash
# 1. Create environment file
cat > .env << EOF
ANTHROPIC_API_KEY=your-key-here
BASE_URL=http://localhost:8000
MINUSPOD_MASTER_PASSPHRASE=long-random-string-you-will-not-lose
EOF

# 2. Create data directory
mkdir -p data

# 3. Run
docker-compose up -d
```

Access the web UI at `http://localhost:8000/ui/` to add and manage feeds.

A fresh install has no password, so the instance is fully open until you set one under Settings > Security: anyone who can reach it can manage feeds, change settings, and download the whole database. Set a password before exposing it beyond localhost.

`MINUSPOD_MASTER_PASSPHRASE` is strongly recommended for production. Without it, provider API keys go into the database as plaintext. Setting it later migrates existing plaintext rows to `enc:v1:` encrypted storage on the next boot, with a mandatory pre-migration SQLite snapshot in `data/backups/`. Restoring a backup requires the same passphrase that created it, so pick a long random value and keep it somewhere separate from the database.

### CPU-only image (no GPU)

No NVIDIA GPU? Pull the CPU variant. It drops the bundled NVIDIA/CUDA Python wheels; the image is around 3 GB instead of ~12 GB. The CPU image is published as a multi-arch manifest covering `linux/amd64` and `linux/arm64`, so Docker on the puller's machine picks the right architecture automatically. The GPU image stays amd64-only.

Reuse the same `.env` and `data/` directory as the Quick Start, then:

```bash
docker compose -f docker-compose.cpu.yml up -d
```

That pulls `ttlequals0/minuspod:cpu` (the floating CPU tag). To pin a specific release, set `MINUSPOD_VERSION=2.29.1-cpu` in your `.env`. The `:latest` tag always points at the GPU image; CPU users should track `:cpu` or a versioned `-cpu` tag.

Local CPU transcription with `faster-whisper` is slow on amd64 and slower on arm64. For anything beyond a quick test, offload Whisper to a remote API in your `.env`:

```
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=https://api.groq.com/openai/v1
WHISPER_API_KEY=gsk_your_key_here
WHISPER_API_MODEL=whisper-large-v3-turbo
```

Groq, OpenAI, or a self-hosted whisper.cpp server (see `docker-compose.whisper.yml`) all work here.

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

Match `OMP_NUM_THREADS` and the cpuset range to your own chip; the values above are for a 6 P-core part with 12 P-threads. The payoff depends on the CPU and model size, so treat the numbers above as one data point rather than a promise. For sustained transcription, a remote Whisper API is still the better answer.

<details>
<summary>Build the CPU image locally</summary>

If you are modifying `Dockerfile.cpu` or want to compile from source, uncomment the `build:` block in `docker-compose.cpu.yml` and run with `--build`:

```bash
docker compose -f docker-compose.cpu.yml up -d --build
```

</details>

---

[< Docs index](README.md) | [Project README](../README.md)
