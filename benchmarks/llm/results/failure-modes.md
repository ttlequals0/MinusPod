# LLM Benchmark: failure-mode sidecar

Companion to `report.md`. Generated ad hoc 2026-08-07T17:24:34Z from `results/raw/calls.jsonl`.

`report.md` scores models on accuracy, cost, and latency. This one covers why a call never produced an answer at all. Some of those reasons disqualify a model for this task regardless of how well it scores on the calls that did succeed.

## Intrinsic vs environmental

The distinction that matters when reading this. An intrinsic failure is a property of the model or its only route, and re-running changes nothing. An environmental failure is a condition of the account, provider capacity, or harness, and it clears on a retry or a settings change. Both look identical in a raw error count.

| Cause | Kind | What it means for a self-hoster |
|---|---|---|
| content-moderation | intrinsic | the provider refuses certain transcripts outright |
| malformed-json | intrinsic | the model emits unparseable output |
| no-native-json | route-intrinsic | model works, but only via prompt injection |
| provider-capacity | environmental | shared-pool contention, clears when load drops |
| account-gating | environmental | a setting on your provider account |
| account-credits | environmental | you ran out of money |
| subscription-cap | environmental | upstream usage window exhausted |

## Every failure observed

Call-level, including calls later recovered on retry.

| Cause | calls | models affected |
|---|---|---|
| subscription-cap | 3003 | `claude-opus-4-8` (855), `claude-sonnet-4-6` (804), `claude-opus-4-7` (794), `claude-sonnet-5` (550) |
| account-gating | 1027 | `meta/muse-spark-1.1` (855), `claude-opus-4-7` (142), `claude-sonnet-4-6` (30) |
| no-native-json | 802 | `deepseek/deepseek-r1-distill-llama-70b` (802) |
| content-moderation | 527 | `stepfun/step-3.7-flash` (527) |
| account-credits | 495 | `cohere/command-r-plus-08-2024` (412), `deepseek/deepseek-r1` (83) |
| provider-capacity | 166 | `thinkingmachines/inkling` (109), `deepseek/deepseek-r1-distill-llama-70b` (53), `claude-sonnet-5` (2), `claude-opus-4-7` (2) |
| malformed-json | 54 | `thinkingmachines/inkling` (23), `deepseek/deepseek-r1` (8), `moonshotai/kimi-k2.6` (4), `moonshotai/kimi-k3` (3) |
| other | 8 | `claude-opus-4-7` (5), `claude-sonnet-5` (3) |
| empty-response | 4 | `nvidia/nemotron-3-super-120b-a12b` (3), `thinkingmachines/inkling` (1) |

## Unresolved after retries

139 of 64125 work units (0.22%).

| Model | unresolved | cause |
|---|---|---|
| `stepfun/step-3.7-flash` | 130 | content-moderation (130) |
| `thinkingmachines/inkling` | 9 | provider-capacity (7), malformed-json (2) |

## Flagged calls

### `deepseek/deepseek-r1-distill-llama-70b`

802 distinct work units flagged: no-native-json (802)

802 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 171 | 185 | 92% |
| `ep-glt1412515089-373d5ba5007b` | 112 | 120 | 93% |
| `ep-security-now-audio-2850b24903b2` | 107 | 115 | 93% |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 83 | 90 | 92% |
| `ep-the-tim-dillon-show-f62bd5fa1cfe` | 59 | 60 | 98% |
| `ep-oxide-and-friends-ce789ff5b62e` | 57 | 65 | 88% |
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 45 | 45 | 100% |
| `ep-crime-junkie-8ce498f299d7` | 35 | 35 | 100% |
| `ep-daily-tech-news-show-c1904b8605f7` | 29 | 30 | 97% |
| `ep-daily-tech-news-show-b576979e1fe8` | 25 | 25 | 100% |
| `ep-tosh-show-5f6894439bb6` | 24 | 30 | 80% |
| `ep-daily-gist-chicago-70a82fe93a5c` | 20 | 20 | 100% |
| `ep-it-s-a-thing-e339179dfad6` | 20 | 20 | 100% |
| `ep-ai-cloud-essentials-e8dc897fbd6b` | 15 | 15 | 100% |

171 distinct (episode, window) pairs flagged. 120 flagged in all 5 trials, 51 in only some.

```
Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, 'metadata': {'raw': '{"code":400, "reason":"INVALID_REQUEST_BODY", "message":"model: deepseek/deepseek-r1-distill-llama-70b does not support feature: structured-outputs", "metadata":{}}', 'provider_name': 'Novita', 'is_b
```

### `stepfun/step-3.7-flash`

132 distinct work units flagged: content-moderation (130), malformed-json (2)

2 later recovered on retry, 130 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 115 | 185 | 62% |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 15 | 90 | 17% |
| `ep-glt1412515089-373d5ba5007b` | 1 | 120 | 1% |
| `ep-security-now-audio-2850b24903b2` | 1 | 115 | 1% |

28 distinct (episode, window) pairs flagged. 26 flagged in all 5 trials, 2 in only some.

```
Error code: 451 - {'error': {'message': 'Provider returned error', 'code': 451, 'metadata': {'raw': '{"error":{"message":"The content you provided or machine outputted is blocked.","type":"censorship_blocked"}}', 'provider_name': 'StepFun', 'is_byok': False}}, 'user_id': 'user_3Axgz92LiCKJYG9UjIpfkp
```

### `thinkingmachines/inkling`

22 distinct work units flagged: malformed-json (22)

15 later recovered on retry, 7 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-security-now-audio-2850b24903b2` | 7 | 115 | 6% |
| `ep-glt1412515089-373d5ba5007b` | 4 | 120 | 3% |
| `ep-oxide-and-friends-ce789ff5b62e` | 3 | 65 | 5% |
| `ep-it-s-a-thing-e339179dfad6` | 2 | 20 | 10% |
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 2 | 45 | 4% |
| `ep-daily-gist-chicago-70a82fe93a5c` | 1 | 20 | 5% |
| `ep-daily-tech-news-show-b576979e1fe8` | 1 | 25 | 4% |
| `ep-daily-tech-news-show-c1904b8605f7` | 1 | 30 | 3% |
| `ep-drink-champs-30c9a2d49f13` | 1 | 185 | 1% |

19 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 19 in only some.

```
Expecting value: line 271 column 1 (char 1485)
```

### `deepseek/deepseek-r1`

8 distinct work units flagged: malformed-json (8)

8 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-the-tim-dillon-show-f62bd5fa1cfe` | 2 | 60 | 3% |
| `ep-drink-champs-30c9a2d49f13` | 1 | 185 | 1% |
| `ep-glt1412515089-373d5ba5007b` | 1 | 120 | 1% |
| `ep-security-now-audio-2850b24903b2` | 1 | 115 | 1% |
| `ep-crime-junkie-8ce498f299d7` | 1 | 35 | 3% |
| `ep-daily-gist-chicago-70a82fe93a5c` | 1 | 20 | 5% |
| `ep-daily-tech-news-show-b576979e1fe8` | 1 | 25 | 4% |

8 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 8 in only some.

```
Expecting value: line 337 column 1 (char 1848)
```

### `moonshotai/kimi-k2.6`

4 distinct work units flagged: malformed-json (4)

4 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-glt1412515089-373d5ba5007b` | 2 | 120 | 2% |
| `ep-the-tim-dillon-show-f62bd5fa1cfe` | 2 | 60 | 3% |

4 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 4 in only some.

```
Expecting value: line 169 column 1 (char 924)
```

### `moonshotai/kimi-k3`

3 distinct work units flagged: malformed-json (3)

3 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 2 | 185 | 1% |
| `ep-daily-tech-news-show-c1904b8605f7` | 1 | 30 | 3% |

3 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 3 in only some.

```
Expecting value: line 461 column 1 (char 2530)
```

### `qwen/qwen3-8b`

3 distinct work units flagged: malformed-json (3)

3 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 2 | 185 | 1% |
| `ep-glt1412515089-373d5ba5007b` | 1 | 120 | 1% |

3 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 3 in only some.

```
Expecting value: line 507 column 1 (char 2783)
```

### `deepseek/deepseek-r1-0528`

3 distinct work units flagged: malformed-json (3)

3 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-daily-tech-news-show-c1904b8605f7` | 1 | 30 | 3% |
| `ep-drink-champs-30c9a2d49f13` | 1 | 185 | 1% |
| `ep-the-tim-dillon-show-f62bd5fa1cfe` | 1 | 60 | 2% |

3 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 3 in only some.

```
Expecting value: line 633 column 1 (char 3476)
```

### `qwen/qwen3.6-plus`

2 distinct work units flagged: malformed-json (2)

2 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 1 | 185 | 1% |
| `ep-the-tim-dillon-show-f62bd5fa1cfe` | 1 | 60 | 2% |

2 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 2 in only some.

```
Expecting value: line 283 column 1 (char 1551)
```

### `inclusionai/ring-2.6-1t`

2 distinct work units flagged: malformed-json (2)

2 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-drink-champs-30c9a2d49f13` | 2 | 185 | 1% |

2 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 2 in only some.

```
Expecting value: line 13 column 1 (char 66)
```

### `qwen/qwen3.5-plus-02-15`

1 distinct work units flagged: malformed-json (1)

1 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-ai-cloud-essentials-e8dc897fbd6b` | 1 | 15 | 7% |

1 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 1 in only some.

```
Expecting value: line 253 column 1 (char 1386)
```

### `meituan/longcat-2.0`

1 distinct work units flagged: malformed-json (1)

1 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-daily-tech-news-show-c1904b8605f7` | 1 | 30 | 3% |

1 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 1 in only some.

```
Expecting value: line 243 column 1 (char 1331)
```

### `nvidia/nemotron-3-super-120b-a12b`

1 distinct work units flagged: malformed-json (1)

1 later recovered on retry, 0 unresolved.

| Episode | flagged | of | rate |
|---|---|---|---|
| `ep-tosh-show-5f6894439bb6` | 1 | 30 | 3% |

1 distinct (episode, window) pairs flagged. 0 flagged in all 5 trials, 1 in only some.

```
Expecting value: line 1659 column 1 (char 9119)
```

