# LLM Providers

[< Docs index](README.md) | [Project README](../README.md)

---

## Using Claude Code Wrapper (Max Subscription)

Instead of using API credits, you can use the [Claude Code OpenAI Wrapper](https://github.com/ttlequals0/claude-code-openai-wrapper) to use your Claude Max subscription instead.

**Quick Start:**

1. Start the wrapper service:
   ```bash
   docker compose --profile wrapper up -d
   ```

2. Authenticate with Claude (first time only):
   ```bash
   docker compose --profile wrapper run --rm claude-wrapper claude auth login
   ```

3. Configure minuspod to use the wrapper by updating your `.env`:
   ```bash
   LLM_PROVIDER=openai-compatible
   OPENAI_BASE_URL=http://claude-wrapper:8000/v1
   OPENAI_API_KEY=not-needed
   ```

4. Restart minuspod:
   ```bash
   docker compose up -d minuspod
   ```

**Other OpenAI-Compatible Endpoints:**

The `openai-compatible` provider can work with other endpoints by configuring `OPENAI_BASE_URL` and `OPENAI_API_KEY` accordingly. The model is selected via the Settings UI.

**Example `.env` for OpenAI-compatible mode:**

```bash
# LLM Configuration (OpenAI-compatible)
LLM_PROVIDER=openai-compatible
OPENAI_BASE_URL=http://claude-wrapper:8000/v1
OPENAI_API_KEY=not-needed

# Server Configuration
BASE_URL=http://localhost:8000
```

Note: The AI model is configured via the Settings UI, not environment variables.

## Using Ollama (Local or Cloud)

[Ollama](https://ollama.com) is an alternative to the Anthropic API. MinusPod supports both flavors:

- **Local** (`http://host:11434`): no auth, no API costs, nothing leaves the machine.
- **Cloud** (`https://ollama.com/api`): same OpenAI-compatible endpoints, just with `Authorization: Bearer <key>` on every request. Free tier works for this pipeline. Grab a key at [ollama.com/settings/keys](https://ollama.com/settings/keys).

Configuration is identical either way: pick `Ollama` in Settings > LLM Provider, set the Base URL, and (for Cloud) paste the key. The key is stored encrypted. Leave it blank for local.

### Heads up about Ollama Cloud model selection

Ollama Cloud's `/v1/models` advertises the full Ollama library, including previews and local-only tags that Cloud won't actually route. The dropdown shows whatever the endpoint returns, so entries like `gemma4:31b`, `kimi-k2:1t`, and `gpt-oss:120b` can appear but 404 when called.

If an episode processes with zero ads and the logs show `{"type":"error","error":{"type":"not_found_error"}}`, the model isn't really on Cloud. The reliable list is at [ollama.com/search?c=cloud](https://ollama.com/search?c=cloud). Cross-check the base name (before the `:`) before saving.

### Setup

1. Install and start Ollama on your host machine
2. Pull a model (see recommendations below): `ollama pull qwen3:14b`
3. Update your `docker-compose.yml`:

```yaml
environment:
  - LLM_PROVIDER=ollama
  - OPENAI_BASE_URL=http://host.docker.internal:11434/v1
  - OPENAI_MODEL=qwen3:14b
```

> **Linux users:** `host.docker.internal` doesn't resolve by default on Linux. Add `extra_hosts: ["host.docker.internal:host-gateway"]` to your Docker service definition.

The `OPENAI_API_KEY` variable is not required for Ollama. Token counts will still be tracked in the UI but cost will always show as $0.00, which is accurate since local inference is free.

---

### Recommended Models

> **Note:** LLMs are non-deterministic. The same prompt against the same model can yield different ads on different runs. The picks below are where I'd start, not where I'd stop tuning.

#### Cloud LLMs (benchmark-tested)

These come from the [offline LLM benchmark](../benchmarks/llm/) included in this repo. The benchmark runs each candidate model against a corpus of human-verified episodes and scores accuracy (F1 at IoU >= 0.5), JSON compliance, latency, and per-episode cost. Full per-model breakdown (precision, recall, boundary accuracy, calibration, latency tail, token efficiency, cross-model agreement) is in [`benchmarks/llm/results/report.md`](../benchmarks/llm/results/report.md). Want to expand the corpus or test more models? See [`benchmarks/llm/CONTRIBUTING.md`](../benchmarks/llm/CONTRIBUTING.md).

| Use case | Model | F1 | Cost / episode | Why |
|---|---|---:|---:|---|
| Best accuracy overall | `qwen/qwen3.5-plus-02-15` (via OpenRouter) | 0.649 | $0.00 | Rank 1 of all 32 models, paid or free. Perfect JSON compliance (1.00). p50 latency 49s, not for live UX, fine for offline batches. Alibaba's content classifier may reject a small fraction of windows. |
| Best accuracy (paid) | `openai/gpt-5.5` | 0.636 | $4.66 | Highest F1 of any paid model. Beats `claude-opus-4-7` on both F1 (0.636 vs 0.618) and cost ($4.66 vs $5.54). JSON compliance 0.87 (the production parser handles the remaining 13% via fallback). |
| Best Anthropic-direct | `claude-opus-4-7` | 0.618 | $5.54 | Rank 3 overall. Perfect JSON compliance (1.00), perfect no-ad pass, lowest false-positive rate. Pick this for direct Anthropic billing or the strictest control side, knowing gpt-5.5 is cheaper and slightly more accurate. |
| Cheap and fast (production) | `google/gemma-4-31b-it` (via OpenRouter) | 0.463 | $0.00 | Free via OpenRouter, p50 latency 1.8s, rank 9 F1. JSON compliance 0.86 (14% of windows take a parser-fallback path; the production parser handles this). |

Caveats:
- Numbers come from a 7-episode corpus (6 ad-bearing, 1 no-ad control), 32 models tested with 31 active (xAI deprecated `grok-4.1-fast` upstream after the May 10 sweep; `grok-4.3` replaces it at F1 0.489 rank 7), 5 trials each, ~14,400 total calls. They will refine as the corpus grows.
- Latency for OpenRouter-routed models reflects routing-layer queueing, not just model compute. Treat it as an availability indicator.
- F1 uses IoU >= 0.5 against human-verified ad spans. A model with F1 0.5 catches half the ads with the right boundaries; a higher F1 means closer to the truth.

#### Local Ollama Models (by VRAM tier)

Note on benchmarking: the offline benchmark in [`benchmarks/llm/`](../benchmarks/llm/) covers cloud-hosted models (OpenRouter, Anthropic direct). Local Ollama runs are not in that sweep. Adding an Ollama provider would let contributors compare local quants apples-to-apples against the cloud numbers. The recommendations below come from author testing, not from a structured benchmark, and should be treated accordingly.

Models are loaded sequentially, not concurrently; VRAM requirements are not additive between passes.

##### Pass 1: First Pass Detection

Hardest task. Contextual reasoning, host-read ads, new sponsors. Use your best model here.

| VRAM | Model | Quantization | Notes |
|------|-------|--------------|-------|
| 8GB | `qwen3:8b` | Q4_K_M | Entry level. Handles standard sponsor reads well. |
| 12GB | `qwen3:14b` | Q4_K_M | Best quality-to-VRAM ratio. **Recommended.** |
| 16GB | `qwen3:14b` | Q5_K_M | Higher quality quant; use if you have headroom. |
| 24GB | `qwen3.5:27b` | Q4_K_M | Strong contextual reasoning. 256K context. |
| 24GB | `qwen3.5:35b` | Q4_K_M | Best quality under 40GB. 256K context. |
| 40GB+ | `qwen3.5:122b` | Q4_K_M | Author's best local option for hard cases. Not yet measured in the cloud benchmark. |

##### Verification Pass

Easier task. Looks for remnants in already-cut audio. Speed matters more than raw accuracy.

| VRAM | Model | Quantization | Notes |
|------|-------|--------------|-------|
| 8GB | `qwen3:4b` | Q8_0 | Fast, good JSON compliance. Verification prompt is simpler. |
| 12GB | `qwen3:8b` | Q5_K_M | Strong JSON compliance, faster than 14B. |
| 16GB | `mistral-nemo:12b` | Q4_K_M | Excellent JSON reliability, fast inference. |
| 24GB | `qwen3:14b` | Q5_K_M | Overkill for verification but uses available VRAM productively. |

##### Chapters

Simplest task. Summarization only, no structured detection. Minimize VRAM usage and latency.

| VRAM | Model | Quantization | Notes |
|------|-------|--------------|-------|
| Any | `qwen3:4b` | Q4_K_M | Sufficient for summarization. Fast. |
| Any | `phi4-mini` | Q4_K_M | Lean alternative, strong instruction following. |
| Any | `llama3.2:3b` | Q4_K_M | Smallest viable option if VRAM is tight. |

> **Example split for 16GB VRAM:** Pass 1 -> `qwen3:14b Q5_K_M` / Verification -> `qwen3:8b Q5_K_M` / Chapters -> `qwen3:4b Q4_K_M`

> **Avoid models under 7B for production use.** JSON reliability degrades significantly at smaller sizes, which causes silent detection failures rather than recoverable errors. See [JSON Reliability Risks](#json-reliability-risks).

---

### Cloud vs. Local: What Changes

Best cloud F1 in the [benchmark](../benchmarks/llm/) is 0.65 (`qwen/qwen3.5-plus-02-15`, free tier on OpenRouter) over 32 models on a 7-episode corpus. `claude-sonnet-4-6` scores 0.38 in the same sweep, well below the leader, so "use Claude" doesn't fix accuracy by itself. The cloud model you pick matters as much as cloud-vs-local does.

The LLM only sees host-read ads that blend into content, new sponsors not yet in the pattern database, and ambiguous mid-rolls without promo codes or URLs. Everything else (audio fingerprinting, text pattern matching, pre/post-roll heuristics, audio-signal enforcement) runs without an LLM and catches a substantial share of ads regardless of model.

| Content type | Cloud-vs-local impact |
|---|---|
| Standard sponsor reads with promo codes / vanity URLs | Minimal: patterns and fingerprinting cover most of these without the LLM |
| Heavy host-read or conversational ad integrations | Noticeable: requires strong contextual reasoning |
| Network-inserted brand-tagline ads (no promo code, no URL) | Moderate: the cloud benchmark shows even frontier models miss roughly a third of these, so don't expect local to outperform |
| New sponsors not in the pattern database | Moderate: depends heavily on model capability |

`qwen3:14b` locally is fine for standard sponsor reads. The gap to cloud-frontier shows up on conversational ad reads that lack clear transitions. To measure the gap on your own content, capture the episode (see [`benchmarks/llm/`](../benchmarks/llm/)) and compare predictions against your verified ground truth.

---

### JSON Reliability Risks

MinusPod's ad detection pipeline requires models to return structured JSON. The Anthropic API enforces this reliably. With Ollama or any open-weights serving, enforcement is model-dependent and failures are more likely.

Failure modes:

- **Malformed JSON**: missing brackets, trailing commas, unquoted keys. The parser tries direct parse, then markdown-fence extraction, then regex scan. Structurally broken JSON falls through all three.
- **Truncated output**: models under memory pressure or processing long transcript windows can cut off mid-response, leaving valid-looking but incomplete JSON.
- **Preamble text**: some models prefix their JSON with conversational text ("Sure, here are the ads I found:"). The parser usually strips this, but it adds fragility.

When a window fails to parse, those ads are silently missed. No UI error; the episode processes normally with gaps in detection coverage.

Cloud models vary widely on this. Benchmark JSON compliance ranges from 0.05 (`openai/o4-mini`, which buries JSON in reasoning chains) and 0.07 (`mistral-7b-instruct-v0.1`, which often returns prose) up to 1.00 (Mistral Medium / Codestral / Large, Qwen 3.5-plus, Claude Opus, Gemini Flash). Claude Haiku 4.5 sits at 0.60 because it wraps every response in markdown code fences; the parser recovers, but the fallback path is slower and more brittle. See the JSON compliance chart in [`benchmarks/llm/results/report.md`](../benchmarks/llm/results/report.md).

Reducing the risk for local runs:

- Use a model of at least 7B parameters
- Prefer Qwen3 or Mistral families (consistently high JSON compliance in author testing)
- Don't run other GPU workloads concurrently; memory pressure increases truncation risk
- Watch the `extraction_method` field in processing logs

Healthy run signal: `extraction_method` reads `json_array_direct` for most calls. Fallback methods (`markdown_code_block`, `regex_*`) mean the model isn't returning clean JSON. Frequent fallback in production means you should upgrade the model.
## Using OpenRouter

[OpenRouter](https://openrouter.ai) is a unified API that routes to 200+ models (Claude, GPT, Gemini, open-weights) with one API key. OpenRouter is supported as an **LLM provider only**: it does not support the `/v1/audio/transcriptions` endpoint required for Whisper transcription. For transcription without a GPU, use a [remote Whisper backend](transcription.md) such as Groq.

### Setup

1. Get an API key from [openrouter.ai/keys](https://openrouter.ai/keys)
2. Add these to your `.env`, then start with `docker-compose.yml` (GPU) or `docker-compose.cpu.yml` (no GPU):

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

### Model Selection

Change the model in the Settings UI or with the `OPENAI_MODEL` env var. Any [OpenRouter model ID](https://openrouter.ai/models) works:

- `anthropic/claude-sonnet-4-5`: Claude Sonnet via OpenRouter
- `openai/gpt-4o`: GPT-4o via OpenRouter
- `google/gemini-2.5-flash-preview`: Gemini Flash via OpenRouter

All of these can be changed at runtime from the Settings UI. No container restart needed.

## LLM Pricing

MinusPod tracks token usage and cost for every LLM call. The Settings page and `GET /api/v1/system/token-usage` show per-model breakdowns.

### Where pricing data comes from

Pricing is fetched automatically based on your configured provider:

| Provider | Source | Method |
|----------|--------|--------|
| Anthropic | [pricepertoken.com](https://pricepertoken.com) | HTML scrape |
| OpenRouter | OpenRouter API (`/api/v1/models`) | JSON API |
| OpenAI, Groq, Mistral, DeepSeek, xAI, Together, Fireworks, Perplexity, Google | [pricepertoken.com](https://pricepertoken.com) | HTML scrape |
| Ollama / localhost | N/A | Always $0 |

Pricing refreshes once every 24 hours in the background. You can also force a refresh from the API:

```bash
curl -X POST http://your-server:8000/api/v1/system/model-pricing/refresh
```

Or view current pricing:

```bash
curl http://your-server:8000/api/v1/system/model-pricing
```

### How model matching works

Different sources use different names for the same model. A normalization step strips provider prefixes, date suffixes, and punctuation so that `claude-sonnet-4-5-20250929` (Anthropic API), `anthropic/claude-sonnet-4-5` (OpenRouter), and `Claude Sonnet 4.5` (pricepertoken.com display name) all resolve to the same pricing entry.

### Offline / air-gapped installs

If the pricing fetch fails on startup and no pricing data exists in the database, MinusPod seeds from a built-in table of Anthropic model prices. Non-Anthropic models will show $0 until the next successful fetch. Existing cached pricing in the database is never lost on fetch failure.

### Pricing accuracy

Pricing data comes from third-party sources and may lag behind provider announcements. Check your provider's billing dashboard for authoritative cost figures. MinusPod's cost tracking is an estimate for convenience, not a billing system.

---

[< Docs index](README.md) | [Project README](../README.md)
