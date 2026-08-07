# JSON parsing and content moderation

Companion to `report.md`, built from `results/raw/calls.jsonl` and the stored response bodies in `results/raw/responses/`.

`report.md` scores models on accuracy, cost, and latency, but only on calls that produced something the parser could read. This covers the two failures retrying will never fix: the model refuses the transcript, or it cannot reliably emit the JSON it was asked for.

Both hurt more than the error counts suggest. A refusal puts whole episodes out of reach. A parse failure that lands on an empty result is scored as "found no ads", so it drags recall down instead of showing up as an error.

## Content moderation

`stepfun/step-3.7-flash` is the only model in the roster that refuses content.

527 call attempts were blocked, covering 130 work units that never completed. Each unit was retried up to 21 times across separate passes and blocked every time. That is deterministic, not a rate limit or a bad draw.

The provider returns HTTP 451:

```
Error code: 451 - {'error': {'message': 'Provider returned error', 'code': 451,
 'metadata': {'raw': '{"error":{"message":"The content you provided or machine
 outputted is blocked.","type":"censorship_blocked"}}',
 'provider_name': 'StepFun', 'is_byok': False}}}
```

The block is on the transcript, not on the ads. It clusters in the two corpus episodes with explicit conversational content:

| Episode | windows blocked | of | share |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 23 | 37 | 62% |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 3 | 18 | 17% |

Blocked window indices, for anyone who wants to reproduce it:

- `ep-drink-champs-30c9a2d49f13`: 4, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20, 22, 23, 24, 25, 26, 27, 29, 30, 31, 34, 35
- `ep-the-brilliant-idiots-0bb9bf634c8e`: 0, 7, 8

The model's F1 in `report.md` is computed from the windows it did answer, so it is not comparable to the rest of the table. If your library contains anything a moderation filter dislikes, this model will silently skip those episodes.

## Bad JSON

The prompt asks for a JSON array. 30,279 of 64,125 calls returned exactly that. The rest needed help, and 3,862 could not be helped at all.

Three failure shapes, separable in the raw data:

- **No output.** The response body is empty. Usually a reasoning model that spent its entire token budget thinking and emitted nothing (`stop_reason: length`, `output_tokens: 4096`, zero content).
- **Unparseable prose.** The model answered in English instead of JSON, or wrapped the answer in commentary the fallback parser could not recover.
- **Recovered.** Real JSON was in there, but the parser had to strip markdown fences, run a regex, or reconstruct a truncated object to get at it. These calls score, but the model is one prompt change away from breaking.

| Model | no output | unparseable prose | recovered | of |
|---|---|---|---|---|
| `openai/o4-mini` | 778 | 0 | 0 | 855 |
| `qwen/qwen3-8b` | 0 | 760 | 15 | 855 |
| `thinkingmachines/inkling` | 262 | 168 | 83 | 846 |
| `meituan/longcat-2.0` | 428 | 1 | 3 | 855 |
| `moonshotai/kimi-k2.6` | 249 | 5 | 6 | 855 |
| `qwen/qwen3.5-27b` | 252 | 1 | 1 | 855 |
| `thinkingmachines/inkling-small` | 171 | 79 | 40 | 855 |
| `tencent/hy3` | 194 | 42 | 18 | 855 |
| `stepfun/step-3.7-flash` | 233 | 1 | 4 | 725 |
| `qwen/qwen3.8-max` | 230 | 0 | 1 | 855 |
| `deepseek/deepseek-v4-flash-0731` | 155 | 3 | 4 | 855 |
| `deepseek/deepseek-r1-distill-llama-70b` | 50 | 85 | 684 | 855 |
| `qwen/qwen3-14b` | 11 | 120 | 2 | 855 |
| `nvidia/nemotron-3-super-120b-a12b` | 122 | 5 | 7 | 855 |
| `inclusionai/ring-2.6-1t` | 96 | 14 | 3 | 855 |
| `deepseek/deepseek-r1-0528` | 96 | 0 | 4 | 855 |
| `openai/gpt-oss-20b` | 74 | 25 | 3 | 855 |
| `qwen/qwen3.6-plus` | 83 | 3 | 21 | 855 |
| `deepseek/deepseek-r1` | 84 | 1 | 3 | 855 |
| `moonshotai/kimi-k3` | 50 | 0 | 230 | 855 |

`report.md` has the full extraction-method breakdown for every model in the parser stress section.

### Examples

Real response bodies, truncated for length. Every one of these was scored.

**Prose instead of JSON.** `qwen/qwen3-8b`, `ep-drink-champs-30c9a2d49f13` window 10, 856 output tokens, parsed 0 ads:

```
">// No advertisement segments found in this window. All content is part of the
interview [...] No sponsor reads, product promotions, or platform-inserted ads
were identified. [...]//</s> [{}] // Empty array indicates no ads detect
```

The model got the answer right and still scored nothing recoverable. This shape is 760 of its 855 calls.

**A correct answer buried in commentary.** `deepseek/deepseek-r1-distill-llama-70b`, `ep-ai-cloud-essentials-e8dc897fbd6b` window 0:

~~~
Based on the analysis of the provided transcript window (0.0-600.0s), the
following advertisement segment is identified:

```json
[
  {
    "start": 58.0,
    "end": 80.1,
    "confidence": 0.95,
    ...
~~~

Recoverable, and the fence-stripping path handles it. 684 of this model's 855 calls needed one of the fallback parsers.

**The system prompt echoed back.** `xiaomi/mimo-v2.5`, `ep-daily-tech-news-show-b576979e1fe8` window 1:

~~~
{"system": "You are an AI assistant that helps users identify advertisements in
podcast transcripts."}
```
[{"start": 636.8, "end": 671.5, "confidence": 0.98, ...}]
~~~

The array after it is valid. The object before it is not part of any requested schema.

**Degenerate output that eats the token cap.** `inclusionai/ring-2.6-1t`, `ep-drink-champs-30c9a2d49f13` window 33, 4096 output tokens, parsed 0 ads:

```
{
  "[]"




[... 2,800 characters of newlines until the cap ...]
```

**Truncated mid-object at the cap.** `deepseek/deepseek-r1-0528`, `ep-daily-tech-news-show-c1904b8605f7` window 5, 4096 output tokens:

```
[
  {
    "start": 2246.0,
    "end": 2314.4,
    "confidence": 0
```

The truncation-recovery path salvages the one complete field set it can see. Anything the model would have listed after this is lost.

**One stray character.** `deepseek/deepseek-v4-pro`, `ep-glt1412515089-373d5ba5007b` window 20:

```
[]`
```

And `meta-llama/llama-4-scout`, `ep-ai-cloud-essentials-e8dc897fbd6b` window 0, at 2 output tokens:

```
": []}
```

Both are one keystroke from valid. Neither parses.

**Empty body, full token bill.** `deepseek/deepseek-r1`, `ep-daily-tech-news-show-b576979e1fe8` window 4: 4096 output tokens, `stop_reason: length`, zero characters of content. You pay for the reasoning and get nothing. This is the single most common failure in the table above.

### What is not in here

54 calls errored with `JSONDecodeError: Expecting value: line N column 1`. These are not model output. Every one has zero input tokens, zero output tokens, no stored response body, a wall time near 97 seconds, and a line-to-character ratio of exactly 5.5. That is the HTTP client failing to decode a response, not a model emitting bad JSON. They count as transport errors, not against any model's parse rate.

Rate limits, credit exhaustion, and upstream capacity are also out of scope. They clear on a retry and say nothing about the model.

## Reading this alongside the report

A model near the top of the F1 table with a high "no output" count is not as good as it looks. It scored well on the calls that came back; the ones that did not were counted as finding nothing. Check both tables before you pick one.
