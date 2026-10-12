<p align="center">
  <img src="frontend/public/logo.png" alt="MinusPod" width="400" />
</p>

MinusPod is a self-hosted server that removes ads from podcasts before you ever hit play. It transcribes episodes locally with faster-whisper or Parakeet (or through a remote API), uses an LLM to detect and cut ad segments, and builds cross-episode ad patterns from your corrections so repeat sponsors get caught without re-asking the LLM. Bring your own LLM: Claude, Ollama, OpenRouter, OpenAI-compatible providers, or System One.

<p align="center">
  <img src="docs/screenshots/dashboard-desktop-hulu.png" alt="MinusPod dashboard on desktop" width="600" />
  &nbsp;
  <img src="docs/screenshots/dashboard-mobile-hulu.png" alt="MinusPod dashboard on mobile" width="185" />
</p>

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [System One](#system-one)
- [Disclaimer](#disclaimer)
- [License](#license)
- [LLM disclosure](#llm-disclosure)

## Features

**Ad detection**
- First-pass LLM detection over sliding windows, plus an automatic verification pass on the re-cut audio, skippable per feed
- Native System One support for detection and verification, with experimental review. [Details](docs/system-one.md)
- Optional ad-reviewer stage that confirms, adjusts, or rejects each cut and can resurrect borderline detections
- Audio-side signals: loudness analysis, DAI transition detection, a cross-fetch differential against a second download, pre/post-roll, and a VAD-gap detector for spans Whisper drops
- Per-feed audio cue detection that snaps cuts to a show's jingle or stinger
- [Publisher transcript comparison](docs/transcript-differential.md) adds evidence for speech omitted from an upstream transcript; unmatched gaps wait for review
- Confidence scoring with a review queue filterable by hold reason and reviewer adjustment; rejected detections stay visible for auditing
- Every marker carries a segment category (sponsor, cross-promo, self-promo, interaction, and opt-in intro/outro/recap), each resolving to remove, beep, keep, or mark (left in the audio and published as a skippable chapter when chapters are enabled), set globally or per feed and defaulting to remove until configured
- Five per-feed [processing modes](docs/workflows.md) (standard, keep content only, cue-only, skip detection, pass-through), plus per-episode pass-through

**Transcription**
- Local transcription with faster-whisper (Whisper) or NVIDIA Parakeet TDT 0.6B v3 on GPU or CPU, or a remote OpenAI-compatible API
- Works with whisper.cpp, Groq, OpenAI Whisper, and OpenVINO (Intel GPU)

**LLM providers**
- Bring your own: Anthropic, OpenRouter, Ollama, System One, or any OpenAI-compatible endpoint, switchable at runtime
- Per-stage tuning (model, temperature, tokens, reasoning) and editable prompts with per-pass overrides
- Independent Provider A/B routing, plus [failover](docs/failover.md) to a separately configured LLM or transcriber during outages
- Provider keys encrypted with AES-256-GCM behind a master passphrase
- Rate-limit hold on provider 429s and an offline queue for down self-hosted endpoints. [Details](docs/configuration.md)

**Patterns and sponsors**
- Cross-episode pattern learning from your corrections, scoped podcast to network to global
- Sponsor list with aliases and normalizations
- Opt-in community pattern sync, with one-PR submission back, and pattern import and export
- Experimental [Pattern Cleanup](docs/pattern-cleanup.md): scheduled or manual suggestions to trim, split, rename, retire, or flag unreliable learned patterns, with approval and undo

**Publishing**
- Re-cut RSS feeds served per podcast, with versioned audio files
- Replacement sound at each cut, loudness leveling, and lossless MP3 stream copy for compatible cuts. [Details](docs/configuration.md)
- Podcasting 2.0: regenerated transcripts and chapters, AI-content disclosure, value-for-value tags passed through
- Optional Podping listener refreshes a feed within seconds of the host announcing a new episode, on top of scheduled polling
- OPML import/export, including selected subscriptions with ad-free URLs, and an optional cover-art badge that marks the re-feed
- Local feeds: build a feed from your own audio files instead of an upstream RSS feed, with single or bulk episode upload, a dry-run import preview, and the same ad-removal pipeline as a subscribed feed
- Podcast search from Add Feed, backed by Podcast Index. [Details](docs/feeds-and-usage.md)
- Optional Recents feed combines processed episodes published on or after its creation date across subscribed and local feeds, including feeds added later

**Interface and ops**
- Web UI with a waveform ad editor, plus feed, episode, queue, search, pattern, sponsor, history, stats, and settings views
- Stats and cost analytics: ads cut, time saved, an LLM cost ledger (per-episode, per-run, and per-phase spend, with a drill-down to calls), plus System One, reviewer, and Pattern Cleanup stats. [Details](docs/web-interface.md)
- Retention controls (global or per feed, including a never-delete archive mode), scheduled and downloadable backups (encrypted when a master passphrase is set), email and webhook notifications, and a full REST API with OpenAPI
- Shared-password UI and API auth, with optional feed keys or per-feed subscriber keys for RSS; runs behind a reverse proxy or Cloudflare tunnel
- Runtime configuration export and restore with a preview before apply. [Details](docs/configuration-import-export.md)

## How it works

The transcriber (faster-whisper, Parakeet, or a remote API) transcribes the episode, an LLM finds the ad segments in the transcript, FFmpeg cuts them, and Flask serves the modified feed and audio. Full pipeline detail (verification pass, sliding windows, pattern learning, audio analysis) is in [docs/how-it-works.md](docs/how-it-works.md).

## Requirements

- Docker with NVIDIA GPU support (for local transcription), **or** a [remote transcription backend](docs/transcription.md) (no GPU needed)
- Anthropic API key, [OpenRouter](https://openrouter.ai) API key, [Ollama](https://ollama.com) for local inference, a System One credential, **or** any OpenAI-compatible endpoint

Memory and VRAM tables are in [docs/installation.md](docs/installation.md).

## Quick start

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

# 3. Run
docker-compose up -d
```

Access the web UI at `http://localhost:8000/ui/` to add and manage feeds.

Compose requires an application password before it serves the normal API. Set the first password from the same host. For remote setup, put a temporary `MINUSPOD_SETUP_TOKEN` in `.env`, send it in the `X-MinusPod-Setup-Token` header when setting the first password, then remove it. Compose also prevents unauthenticated feed requests from starting paid processing. Enable Authenticated Feeds, or set `MINUSPOD_ALLOW_PUBLIC_PROCESSING=true` if public just-in-time processing is intentional.

`MINUSPOD_MASTER_PASSPHRASE` is strongly recommended for production. Without it, provider API keys go into the database as plaintext. Setting it later migrates existing plaintext rows to `enc:v1:` encrypted storage on the next boot, with a mandatory pre-migration SQLite snapshot in `data/backups/`. Restoring an encrypted API download requires the same passphrase that created it, so pick a long random value and keep it somewhere separate from the database.

**No NVIDIA GPU?** Pull the CPU variant (`docker compose -f docker-compose.cpu.yml up -d`; multi-arch, runs natively on amd64 and arm64) and offload transcription to a remote API. Full CPU setup is in [docs/installation.md](docs/installation.md).

**Stable or edge?** `:latest` (GPU) and `:cpu` follow every release; several can land in one day. For a slower, vetted track, set `MINUSPOD_VERSION=stable` (GPU compose) or `MINUSPOD_VERSION=stable-cpu` (CPU compose) in your `.env` to pin `ttlequals0/minuspod:stable` / `:stable-cpu` instead. Stable tags only move to releases that have soaked in production; see the [releases page](https://github.com/ttlequals0/MinusPod/releases) for curated notes on each.

## Documentation

| Topic | |
|---|---|
| [Installation](docs/installation.md) | Requirements, quick start, CPU image, Intel hybrid CPU tuning |
| [Environment Variables](docs/environment-variables.md) | Every env var, grouped by how often you touch it |
| [LLM Providers](docs/llm-providers.md) | Claude Code wrapper, Ollama, OpenRouter, recommended models, pricing |
| [Transcription](docs/transcription.md) | faster-whisper and Parakeet backends, choosing between them, GPU compute types, whisper.cpp, Groq, OpenAI Whisper, timeouts |
| [Finding Feeds & Usage](docs/feeds-and-usage.md) | Podcast search, finding RSS feeds, Audiobookshelf |
| [Web Interface](docs/web-interface.md) | Management UI, ad editor workflow, screenshots |
| [How It Works & Detection Pipeline](docs/how-it-works.md) | Verification pass, sliding windows, queue, validation, pattern learning, audio analysis |
| [Episode Processing Workflows](docs/workflows.md) | Visual map of the pipeline, the five processing modes, re-runs, and failure paths |
| [Configuration & Experiments](docs/configuration.md) | Settings, per-stage LLM tuning, VAD gap detector, ad reviewer, reprocessing, community patterns, segment categories, episode filters, queue priority, offline queue, rate-limit hold, Whisper pool, scheduled backups |
| [Security, Storage & Custom Assets](docs/security-and-storage.md) | Remote access, login lockout, authenticated feeds and subscriber keys, backups and restore, custom markers |
| [Audio Cue Detection](docs/audio-cues.md) | Per-feed cue templates, the find-audio-cues suggestion scan, settings, and tuning |
| [Community Patterns](patterns/README.md) | Crowdsourced ad pattern set: opt-in manifest sync, file format, and how to contribute |
| [Pattern Cleanup](docs/pattern-cleanup.md) | Scheduled or manual review of learned patterns: trims, splits, renames, retirements, approve, reject, undo |
| [Upstream Transcript Differential](docs/transcript-differential.md) | Diffing the Whisper transcript against the publisher's ad-free transcript, how gaps corroborate or hold |
| [System One](docs/system-one.md) | Native TypeSafe and compatible providers, supported phases, profiles, migration, statistics |
| [Failover](docs/failover.md) | Standby LLM provider and transcriber, triggers, health probes, recovery, manual control |
| [Local Feeds](docs/local-feeds.md) | Build a feed from your own audio files: creation, uploads, bulk archive import, naming scheme, JSON sidecar schema |
| [Configuration Import / Export](docs/configuration-import-export.md) | Export and restore global settings and selected feed configuration |
| [Intel GPU Transcription (OpenVINO)](docs/transcription-openvino.md) | Offload Whisper to an Intel GPU via the OpenVINO Model Server |
| [API & Webhooks](docs/api-and-webhooks.md) | REST endpoints, webhook events, payload templates, signing, email notifications |
| [Podcasting 2.0](docs/podcasting-2.0.md) | What MinusPod emits, regenerates, and deliberately strips from the Podcast Namespace, and why |
| [Deployment Runbook](docs/DEPLOYMENT.md) | Operational runbook |
| [Releasing & Channels](docs/releasing.md) | Stable vs edge channels, how releases are tagged and promoted |
| [Container Security Updates](docs/container-security.md) | Pinned security patches for media libraries and base packages in the GPU and CPU images |
| [LLM Benchmark Report](benchmarks/llm/results/report.md) | Per-model F1, JSON compliance, latency, and cost across the benchmarked models |
| [Glossary](docs/glossary.md) | Every term the app uses, defined and linked to the docs that cover it |

Or browse the [full docs index](docs/README.md).

## Contributing

Bug reports, feature requests, enhancements, and general feedback are all welcome. Open an issue using one of the templates, or send a pull request if you already have a fix in hand. For larger changes, open an issue first so we can talk through the approach before you write code. Two areas have their own contribution flows: ad patterns ([Community Patterns](patterns/README.md)) and benchmark results ([Benchmark Contributing](benchmarks/llm/CONTRIBUTING.md)). Fork-derived features and community bug reports are credited in the CHANGELOG.md entry that ships them; merged commits are credited automatically on the repo's contributors graph.

## System One

MinusPod has native TypeSafe and System One-compatible providers for detection and verification. Ad category classification is supported; the optional reviewer is experimental. Chapter generation and Pattern Cleanup require a chat-completion provider. See [System One setup, phase support, migration, and statistics](docs/system-one.md).

## Disclaimer

This tool is for personal use only. Only use it with podcasts you have permission to modify or where such modification is permitted under applicable laws. Respect content creators and their terms of service.

## License

MIT

## LLM disclosure

This project was developed using AI agents as a pair programmer. It was NOT vibe coded. For context, I'm a systems engineer who also writes code professionally with 15+ years of experience. The codebase follows engineering best practices, and all architecture and design decisions were made by me, not by AI. All code generated by LLMs was reviewed and tested by me, a human.
