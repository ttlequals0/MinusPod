# MinusPod Documentation

Full documentation for MinusPod. Start with the [project README](../README.md) for a quick install, then come here for the details.

## Contents

- [How It Works & Detection Pipeline](how-it-works.md) - the transcription -> detection -> cut pipeline, verification pass, sliding windows, processing queue, validation, pattern learning, audio analysis
- [Installation](installation.md) - requirements, quick start, CPU-only image, Intel hybrid CPU tuning
- [Web Interface](web-interface.md) - the management UI, ad editor workflow, screenshots
- [Configuration & Experiments](configuration.md) - settings, per-stage LLM tuning, VAD gap detector, provider keys, ad reviewer, community patterns, scheduled backups
- [Audio Cue Detection](audio-cues.md) - per-feed cue templates, the find-audio-cues scan, cue types, settings, and tuning
- [Environment Variables](environment-variables.md) - every env var, grouped by how often you touch it
- [LLM Providers](llm-providers.md) - Claude Code wrapper, Ollama (local/cloud), OpenRouter, recommended models, pricing
- [Whisper / Transcription](transcription.md) - GPU compute types, whisper.cpp, Groq, OpenAI Whisper, language, timeouts
- [Intel GPU Transcription (OpenVINO)](transcription-openvino.md) - offload Whisper to an Intel GPU via OpenVINO Model Server
- [Finding Feeds & Usage](feeds-and-usage.md) - podcast search, finding RSS feeds, Audiobookshelf
- [API & Webhooks](api-and-webhooks.md) - REST endpoints, webhook events, payload templates, signing
- [Security, Storage & Custom Assets](security-and-storage.md) - remote access, login lockout, backups, custom ad markers
- [Podcasting 2.0](podcasting-2.0.md) - what MinusPod emits, regenerates, and deliberately strips from the Podcast Namespace, and why
- [Glossary](glossary.md) - every term the app uses, defined and linked to the docs that cover it
- [Deployment Runbook](DEPLOYMENT.md) - operational runbook
- [Releasing & Channels](releasing.md) - stable vs edge channels, how releases are tagged and promoted

[< Project README](../README.md)
