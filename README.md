<p align="center">
  <img src="frontend/public/logo.png" alt="MinusPod" width="400" />
</p>

MinusPod is a self-hosted server that removes ads before you ever hit play. It transcribes episodes with Whisper, uses an LLM to detect and cut ad segments, and builds cross-episode ad patterns from your corrections so repeat sponsors get caught without re-asking the LLM. Bring your own LLM: Claude, Ollama, OpenRouter, or any OpenAI-compatible provider.

## How It Works

1. **Transcription** - Whisper converts audio to text with timestamps (local GPU via faster-whisper, or remote API via OpenAI-compatible endpoint)
2. **Ad Detection** - An LLM analyzes the transcript to identify ad segments, with an automatic verification pass
3. **Audio Processing** - FFmpeg removes detected ads and inserts short audio markers
4. **Serving** - Flask serves modified RSS feeds and processed audio files

Processing happens on-demand when you play an episode, or automatically when new episodes appear. An episode is processed once; processing time depends on episode length, hardware, and chosen models. After processing, the output is stored on disk and served directly on subsequent plays.

Full pipeline detail (verification pass, sliding windows, pattern learning, audio analysis) is in [docs/how-it-works.md](docs/how-it-works.md).

## Requirements

- Docker with NVIDIA GPU support (for local Whisper), **or** a [remote Whisper backend](docs/transcription.md) (no GPU needed)
- Anthropic API key, [OpenRouter](https://openrouter.ai) API key, [Ollama](https://ollama.com) for local inference, **or** any OpenAI-compatible endpoint

Memory and VRAM tables are in [docs/installation.md](docs/installation.md).

## Quick Start

```bash
# 1. Create environment file
cat > .env << EOF
ANTHROPIC_API_KEY=your-key-here
BASE_URL=http://localhost:8000
APP_PASSWORD=your-password
MINUSPOD_MASTER_PASSPHRASE=long-random-string-you-will-not-lose
EOF

# 2. Create data directory
mkdir -p data

# 3. Run
docker-compose up -d
```

Access the web UI at `http://localhost:8000/ui/` to add and manage feeds.

`MINUSPOD_MASTER_PASSPHRASE` is strongly recommended for production. Without it, provider API keys go into the database as plaintext. Setting it later migrates existing plaintext rows to `enc:v1:` encrypted storage on the next boot, with a mandatory pre-migration SQLite snapshot in `data/backups/`. Restoring a backup requires the same passphrase that created it, so pick a long random value and keep it somewhere separate from the database.

**No NVIDIA GPU?** Pull the CPU variant (`docker compose -f docker-compose.cpu.yml up -d`; multi-arch, runs natively on amd64 and arm64) and offload Whisper to a remote API. Full CPU setup and the 2.0.0+ upgrade notes are in [docs/installation.md](docs/installation.md).

## Documentation

| Topic | |
|---|---|
| [How It Works & Detection Pipeline](docs/how-it-works.md) | Verification pass, sliding windows, queue, validation, pattern learning, audio analysis |
| [Installation & Upgrading](docs/installation.md) | Requirements, quick start, CPU image, upgrading to 2.0.0+ |
| [Web Interface](docs/web-interface.md) | Management UI, ad editor workflow, screenshots |
| [Configuration & Experiments](docs/configuration.md) | Settings, per-stage LLM tuning, VAD gap detector, ad reviewer, audio cue detection, reprocessing, community patterns |
| [Community Patterns](patterns/README.md) | Crowdsourced ad pattern set: opt-in manifest sync, file format, and how to contribute |
| [Environment Variables](docs/environment-variables.md) | Every env var, grouped by how often you touch it |
| [LLM Providers](docs/llm-providers.md) | Claude Code wrapper, Ollama, OpenRouter, recommended models, pricing |
| [Whisper / Transcription](docs/transcription.md) | GPU compute types, whisper.cpp, Groq, OpenAI Whisper, timeouts |
| [Finding Feeds & Usage](docs/feeds-and-usage.md) | Podcast search, finding RSS feeds, Audiobookshelf |
| [API & Webhooks](docs/api-and-webhooks.md) | REST endpoints, webhook events, payload templates |
| [Security, Storage & Custom Assets](docs/security-and-storage.md) | Remote access, login lockout, backups, custom markers |
| [Podcasting 2.0](docs/podcasting-2.0.md) | What MinusPod emits, regenerates, and deliberately strips from the Podcast Namespace, and why |
| [Deployment Runbook](docs/DEPLOYMENT.md) | Operational runbook |
| [LLM Benchmark Report](benchmarks/llm/results/report.md) | Per-model F1, JSON compliance, latency, cost across 32 models on a 7-episode corpus |

Or browse the [full docs index](docs/README.md).

## Disclaimer

This tool is for personal use only. Only use it with podcasts you have permission to modify or where such modification is permitted under applicable laws. Respect content creators and their terms of service.

## License

MIT

## LLM Disclosure

This project was developed using AI agents as a pair programmer. It was NOT vibe coded. For context, I'm a systems engineer who also writes code professionally with 15+ years of experience. The codebase follows engineering best practices, and all architecture and design decisions were made by me, not by AI. All code generated by LLMs was reviewed and tested by me, a human.
