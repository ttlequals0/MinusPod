# MinusPod Documentation

Full documentation for MinusPod. Start with the [project README](../README.md) for a quick install, then come here for the details.

## Contents

- [How It Works & Detection Pipeline](how-it-works.md) - the transcription -> detection -> cut pipeline, verification pass, sliding windows, processing queue, validation, pattern learning, audio analysis
- [Installation & Upgrading](installation.md) - requirements, quick start, CPU-only image, upgrading to 2.0.0+
- [Web Interface](web-interface.md) - the management UI, ad editor workflow, screenshots
- [Configuration & Experiments](configuration.md) - settings, per-stage LLM tuning, VAD gap detector, provider keys, ad reviewer, community patterns
- [Environment Variables](environment-variables.md) - every env var, grouped by how often you touch it
- [LLM Providers](llm-providers.md) - Claude Code wrapper, Ollama (local/cloud), OpenRouter, recommended models, pricing
- [Whisper / Transcription](transcription.md) - GPU compute types, whisper.cpp, Groq, OpenAI Whisper, language, timeouts
- [Finding Feeds & Usage](feeds-and-usage.md) - podcast search, finding RSS feeds, Audiobookshelf
- [API & Webhooks](api-and-webhooks.md) - REST endpoints, webhook events, payload templates, signing
- [Security, Storage & Custom Assets](security-and-storage.md) - remote access, login lockout, backups, custom ad markers
- [Deployment Runbook](DEPLOYMENT.md) - operational runbook

[< Project README](../README.md)
