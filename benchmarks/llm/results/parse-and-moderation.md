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

The three blocked windows without the slur are the episode's most sexually explicit stretches; one holds 27 hits, the highest of any window in the episode. The single slur-bearing window that got through has one instance in 2,069 words.

The filter is matching terms, not judging passages. The transcript is a verbatim conversation and the term is in-group usage in a hip-hop interview, but the filter does not make that distinction. The slur is not reproduced here.

### Examples of blocked calls

Five windows, each refused on all 5 trials and on every retry pass with the same 451. `dc` is `ep-drink-champs-30c9a2d49f13`, `bi` is `ep-the-brilliant-idiots-0bb9bf634c8e`.

| Window | Span | Words | Slur | Sexual | Profanity | Ads present |
|---|---|---|---|---|---|---|
| `bi` w0 | 0-600s | 2,091 | 1 | 9 | 13 | 1 |
| `dc` w9 | 3780-4380s | 1,873 | 0 | 27 | 28 | 0 |
| `dc` w12 | 5040-5640s | 1,785 | 10 | 10 | 31 | 0 |
| `dc` w19 | 7980-8580s | 1,794 | 7 | 0 | 25 | 0 |
| `bi` w7 | 2940-3540s | 2,089 | 3 | 4 | 8 | 0 |

**The ones that cost detections.** Six of the 26 blocked windows contain an ad. Windows overlap by 3 minutes, so some of those ads survive in a neighboring window that was not refused, but three do not: two in `dc`, one in `bi`. Of the 12 ads across the two episodes, a quarter were unreachable for this model no matter how many times the run retried.

`bi` window 0 is the clearest case, with the ad in the clear at the top of the file:

```
[0.5s - 16.8s] Hey, sweetie, your mother showed me this ... thing for selling
the car. I'm gonna give it a try. Wish me luck. Me again. I put in the license
plate. It gave me an offer ...
```

A scripted read for a used-car service, textbook material, and the model never saw it. One slur instance in 2,091 words was enough to refuse the window.

**The one with no slur at all.** `dc` window 9 is the counter-example to everything above: zero slur hits, and refused anyway. It carries 27 sexual-language hits, the highest of any window in the episode, so a second trigger exists. It is also the point where the pattern stops being a clean single-term rule.

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

Six real calls: what went in, what came back, and what the raw fields say about the cause. Response bodies are truncated for length. Every one of these was scored.

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

### What is not in here

54 calls errored with `JSONDecodeError: Expecting value: line N column 1`. These are not model output. Every one has zero input tokens, zero output tokens, no stored response body, a wall time near 97 seconds, and a line-to-character ratio of exactly 5.5. That is the HTTP client failing to decode a response, not a model emitting bad JSON. They count as transport errors, not against any model's parse rate.

Rate limits, credit exhaustion, and upstream capacity are also out of scope. They clear on a retry and say nothing about the model.

## Reading this alongside the report

A model near the top of the F1 table with a high "no output" count is not as good as it looks. It scored well on the calls that came back; the ones that did not were counted as finding nothing. Check both tables before you pick one.
