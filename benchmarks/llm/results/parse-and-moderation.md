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

### What actually triggered it

Both episodes are profane throughout, so profanity alone does not explain which windows were refused. Measured across the 37 windows of the larger episode, blocked and clean windows use general profanity at almost the same rate: 9.89 versus 9.77 hits per 1,000 words.

One term does separate them. A racial slur appears in 24 windows across the two episodes, and 23 of those were blocked. Going the other way, 23 of the 26 blocked windows contain it.

| Signal | blocked windows | clean windows |
|---|---|---|
| racial slur, per 1,000 words | 1.42 | 0.00 |
| sexual language, per 1,000 words | 4.82 | 2.94 |
| general profanity, per 1,000 words | 9.89 | 9.77 |
| violence, per 1,000 words | 0.96 | 0.33 |

(Rates from `ep-drink-champs-30c9a2d49f13`, the episode with enough of both groups to compare.)

Three blocked windows have no slur in them. One is the most sexually explicit passage in the episode at 27 hits; the other two are unremarkable on every signal measured here, so something outside these categories is triggering them. The single slur-bearing window that got through has one instance in 2,069 words.

The filter is matching terms, not judging passages. The transcript is a verbatim conversation and the term is in-group usage in a hip-hop interview, but the filter does not make that distinction. The slur is not reproduced here.

### Examples of blocked calls

Seven windows, each refused on all 5 trials and on every retry pass with the same 451. `dc` is `ep-drink-champs-30c9a2d49f13`, `bi` is `ep-the-brilliant-idiots-0bb9bf634c8e`. Call IDs for every row are in [Finding these calls in the raw data](#finding-these-calls-in-the-raw-data).

| Window | Span | Words | Slur | Sexual | Profanity | Ads present |
|---|---|---|---|---|---|---|
| `bi` w0 | 0-600s | 2,091 | 1 | 9 | 13 | 1 |
| `dc` w4 | 1680-2280s | 1,719 | 1 | 1 | 19 | 0 |
| `dc` w9 | 3780-4380s | 1,873 | 0 | 27 | 28 | 0 |
| `dc` w12 | 5040-5640s | 1,785 | 10 | 10 | 31 | 0 |
| `dc` w19 | 7980-8580s | 1,794 | 7 | 0 | 25 | 0 |
| `dc` w27 | 11340-11940s | 1,314 | 0 | 9 | 5 | 1 |
| `bi` w7 | 2940-3540s | 2,089 | 3 | 4 | 8 | 0 |

**The ones that cost detections.** Six of the 26 blocked windows contain an ad. Windows overlap by 3 minutes, so some of those ads survive in a neighboring window that was not refused, but three do not: two in `dc`, one in `bi`. Of the 12 ads across the two episodes, a quarter were unreachable for this model no matter how many times the run retried.

`bi` window 0 is the clearest case, with the ad in the clear at the top of the file:

```
[0.5s - 16.8s] Hey, sweetie, your mother showed me this ... thing for selling
the car. I'm gonna give it a try. Wish me luck. Me again. I put in the license
plate. It gave me an offer ...
```

A scripted read for a used-car service, textbook material, and the model never saw it. One slur instance in 2,091 words was enough to refuse the window.

**The ones with no slur at all.** `dc` window 9 and window 27 are the counter-examples to everything above: zero slur hits, refused anyway. Window 9 carries 27 sexual-language hits, the highest in the episode, so a second trigger exists. Window 27 does not have that excuse. It is the shortest window in the table at 1,314 words, and its 9 sexual hits and 5 profanity hits are unremarkable for this episode. It came back 451 on every attempt anyway. Whatever tripped it is not in any category measured here.

**The one that barely registers.** `dc` window 4 has a single slur instance, one sexual hit, and 19 profanity hits in 1,719 words. By every signal except that one word it looks like the windows that passed. It was refused all five times.

**The cleanest test case.** `dc` window 19 has 7 slur instances and zero sexual-language hits, so nothing else in it could plausibly be the trigger. It is a musician's story about a night out in the 1990s:

```
[8025.2s - 8026.8s] Biggie Smalls or Big L?
[8058.6s - 8085.6s] And this ... Puerto Rican named Dee is there. They're her
two friends. And Luke say, I got something for you ...
```

Density does not appear to matter. `dc` window 12 carries 10 instances and `bi` window 7 carries 3, in 2,089 words of two hosts talking about a radio DJ's career, and both were refused the same way. Presence is what counts.

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

### Example calls

Eight real calls: what went in, what came back, and what the raw fields say about the cause. Response bodies are truncated for length. Every one of these was scored.

First, one thing the data rules out. The intuitive guess is that models flail on windows with nothing to report, where the answer is an empty array. The opposite holds. Windows with at least one ad produce unusable output in 12.70% of calls (3,233 of 25,464); windows with no ads do it in 5.74% (2,212 of 38,522). Emitting ad objects is where output goes wrong, not withholding them.

**1. Prose instead of JSON**

- Input: `ep-drink-champs-30c9a2d49f13` window 10, 600s, 1,851 words, 0 ads in ground truth
- Output: `qwen/qwen3-8b`, 856 tokens, `stop_reason: stop`, 0 ads parsed

```
">// No advertisement segments found in this window. All content is part of the
interview [...] No sponsor reads, product promotions, or platform-inserted ads
were identified. [...]//</s> [{}] // Empty array indicates no ads detect
```

Cause: habit, not this input. The model reached the right conclusion and wrote it as commentary with C-style comment markers and a stray `</s>` token. It does this on 760 of its 855 calls regardless of what it is given, so no property of this window explains it.

**2. Empty body, full token bill**

- Input: `ep-daily-tech-news-show-b576979e1fe8` window 4, 393s, 1,047 words, 1 ad in ground truth
- Output: `deepseek/deepseek-r1`, 4,096 tokens, `stop_reason: length`, 0 characters of content

Cause: the reasoning budget consumed the response budget. The model hit the 4096-token cap mid-thought and never reached the part where it writes the answer. You pay for every one of those tokens. This is the single most common failure in the table above.

**3. Truncated mid-object at the cap**

- Input: `ep-daily-tech-news-show-c1904b8605f7` window 5, 214s, 669 words, 1 ad in ground truth
- Output: `deepseek/deepseek-r1-0528`, 4,096 tokens, `stop_reason: length`, 1 ad recovered

```
[
  {
    "start": 2246.0,
    "end": 2314.4,
    "confidence": 0
```

Cause: same cap, one step further along. The model started writing and ran out inside the third field. The input is the shortest of any example here at 669 words, so this is not a long-transcript problem. The truncation-recovery path salvages the fields it can see; anything the model meant to list after this is gone.

**4. Degenerate output**

- Input: `ep-drink-champs-30c9a2d49f13` window 33, 600s, 1,592 words, 0 ads in ground truth
- Output: `inclusionai/ring-2.6-1t`, 4,096 tokens, `stop_reason: length`, 0 ads parsed

```
{
  "[]"




[... 2,800 characters of newlines until the cap ...]
```

Cause: the model quoted the empty array as a string, then fell into a newline loop and emitted whitespace until the cap. It had the right answer in hand and could not stop typing.

**5. A two-token fragment**

- Input: `ep-ai-cloud-essentials-e8dc897fbd6b` window 0, 600s, 1,817 words, 0 ads in ground truth
- Output: `meta-llama/llama-4-scout`, 2 tokens, `stop_reason: stop`, 0 ads parsed

```
": []}
```

Cause: the model treated the prompt as already open inside a JSON object and emitted only the tail it thought was missing. It stopped cleanly, believing it was finished. A close relative is `deepseek/deepseek-v4-pro` on `ep-glt1412515089-373d5ba5007b` window 20, which returned `[]` followed by a stray backtick. Both are one character from valid and neither parses.

**6. The system prompt echoed back**

- Input: `ep-daily-tech-news-show-b576979e1fe8` window 1, 600s, 1,538 words, 1 ad in ground truth
- Output: `xiaomi/mimo-v2.5`, 277 tokens, `stop_reason: stop`, 4 ads recovered

~~~
{"system": "You are an AI assistant that helps users identify advertisements in
podcast transcripts."}
```
[{"start": 636.8, "end": 671.5, "confidence": 0.98, ...}]
~~~

Cause: the model reproduced a paraphrase of its own system prompt as a JSON object, then opened a code fence, then gave the real answer. The array is valid and the parser recovers it. The leading object belongs to no schema anyone asked for.

**7. An empty object instead of an empty array**

- Input: `ep-ai-cloud-essentials-e8dc897fbd6b` window 0, 600s, 1,817 words, 0 ads in ground truth
- Output: `openai/gpt-5.6-sol`, 109 tokens, `stop_reason: stop`, 0 ads parsed

```
{}
```

Cause: the model will not return a bare array. Not once. All 855 of its calls came back wrapped in an object of some kind, `{}` for nothing found and a keyed object otherwise. The parser handles every variant, so this costs nothing but a recovery path. Still, 855 out of 855 is a thorough way to ignore the shape you were asked for.

**8. A wrapper the parser recognizes and then throws away**

- Input: `ep-crime-junkie-8ce498f299d7` window 0, 600s, 1,601 words, 1 ad in ground truth
- Output: `deepseek/deepseek-v4-pro`, 226 tokens, `stop_reason: stop`, 0 ads parsed

```
{
  "segments": [
    {
      "start": 0.0,
      "end": 40.8,
      "confidence": 0.9,
      "category": "sponsor",
      "reason": "Platform-inserted pre-roll ad ... Starts before window.",
      "end_text": "make every connection count"
    },
    ...
```

Cause: this one is not the model's fault. The answer is correct and every field matches the requested schema. The extractor in `src/utils/llm_response.py` recognizes a top-level `segments` list and then filters it to entries where `type == "advertisement"`. The prompt never asks for a `type` field and no model emits one, so the filter always returns empty.

This is not a one-off. All 322 calls that took the `segments` path parsed 0 ads, and 103 of them carried real detections: 190 ad objects in total, every one with `start`, `end`, `confidence`, `category`, `reason`, and `end_text`, none with `type`. Fourteen models are affected, most heavily `deepseek/deepseek-v4-pro` at 205 calls, `nvidia/nemotron-3-ultra-550b-a55b` at 37, and `openai/o3` at 30. Their recall in `report.md` is understated by whatever those calls would have scored.

### Finding these calls in the raw data

Every example above is in [`raw/calls.jsonl`](raw/calls.jsonl), one JSON object per line. Response bodies live in [`raw/responses/`](raw/responses/), sharded by model and keyed by `call_id`.

To pull a call and its response:

```sh
cd benchmarks/llm/results
grep -F '<call_id>' raw/calls.jsonl | python3 -m json.tool
grep -F '<call_id>' raw/responses/<model_with_slashes_and_colons_as_underscores>.jsonl
```

Blocked calls, all `stepfun/step-3.7-flash`, all in `raw/responses/stepfun_step-3.7-flash.jsonl`:

| Window | `call_id` |
|---|---|
| `bi` w0 | `stepfun_step-3.7-flash_ep-the-brilliant-idiots-0bb9bf634c8e_t0_w0_ea40237d8ea3_20260805T183609Z` |
| `dc` w4 | `stepfun_step-3.7-flash_ep-drink-champs-30c9a2d49f13_t0_w4_e206cdbaa9f3_20260805T180444Z` |
| `dc` w9 | `stepfun_step-3.7-flash_ep-drink-champs-30c9a2d49f13_t0_w9_578e89ad4e5a_20260805T180506Z` |
| `dc` w12 | `stepfun_step-3.7-flash_ep-drink-champs-30c9a2d49f13_t0_w12_07b49ae5e43c_20260805T180514Z` |
| `dc` w19 | `stepfun_step-3.7-flash_ep-drink-champs-30c9a2d49f13_t0_w19_48f599003e25_20260805T180523Z` |
| `dc` w27 | `stepfun_step-3.7-flash_ep-drink-champs-30c9a2d49f13_t0_w27_e05fe8081dd8_20260805T180531Z` |
| `bi` w7 | `stepfun_step-3.7-flash_ep-the-brilliant-idiots-0bb9bf634c8e_t0_w7_7cca4af47e2a_20260805T183648Z` |

Those are the trial-0 calls. Each window has 4 more trials plus retry attempts, all with the same outcome.

Bad-JSON examples:

| # | `call_id` |
|---|---|
| 1 | `qwen_qwen3-8b_ep-drink-champs-30c9a2d49f13_t1_w10_b41bae9e7eff_20260805T063315Z` |
| 2 | `deepseek_deepseek-r1_ep-daily-tech-news-show-b576979e1fe8_t3_w4_8373faec5f9e_20260806T030659Z` |
| 3 | `deepseek_deepseek-r1-0528_ep-daily-tech-news-show-c1904b8605f7_t1_w5_4440ba1482c3_20260806T072621Z` |
| 4 | `inclusionai_ring-2.6-1t_ep-drink-champs-30c9a2d49f13_t2_w33_8a6c078997e2_20260805T191627Z` |
| 5 | `meta-llama_llama-4-scout_ep-ai-cloud-essentials-e8dc897fbd6b_t4_w0_e9e422db06c6_20260806T011236Z` |
| 6 | `xiaomi_mimo-v2.5_ep-daily-tech-news-show-b576979e1fe8_t1_w1_97cc9caa7a24_20260805T160008Z` |
| 7 | `openai_gpt-5.6-sol_ep-ai-cloud-essentials-e8dc897fbd6b_t0_w0_f99eb3a15121_20260804T043641Z` |
| 8 | `deepseek_deepseek-v4-pro_ep-crime-junkie-8ce498f299d7_t0_w0_30072d394d6d_20260804T100718Z` |

Example 2 has no stored body, which is the point of it. Example 5's relative, the stray-backtick call, is `deepseek_deepseek-v4-pro_ep-glt1412515089-373d5ba5007b_t1_w20_8881b9fdd85d_20260804T104934Z`.

The input side is reconstructable too. Each call names its `episode_id` and `window_index`, and the transcript it was given is `data/corpus/<episode_id>/windows.json` at that index.

### What is not in here

54 calls errored with `JSONDecodeError: Expecting value: line N column 1`. These are not model output. Every one has zero input tokens, zero output tokens, no stored response body, a wall time near 97 seconds, and a line-to-character ratio of exactly 5.5. That is the HTTP client failing to decode a response, not a model emitting bad JSON. They count as transport errors, not against any model's parse rate.

Rate limits, credit exhaustion, and upstream capacity are also out of scope. They clear on a retry and say nothing about the model.

## Reading this alongside the report

A model near the top of the F1 table with a high "no output" count is not as good as it looks. It scored well on the calls that came back; the ones that did not were counted as finding nothing. Check both tables before you pick one.
