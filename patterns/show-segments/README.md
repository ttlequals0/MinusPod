# Show segments

One file per entry, `seg-<first 8 hex digits of community_id>.json`. An entry describes one recurring
non-sponsor segment of one show (host asks and nudges, self-promo, a sibling-show plug, an intro or an
outro) without its text or the show's name. `src/tools/generate_manifest.py` lists the files under the
`show_segments` key of `patterns/community/index.json`; clients that predate the key ignore it.

| Field | Meaning |
|---|---|
| `kind`, `format_version` | `show_segment`, `1` |
| `community_id`, `version` | UUID; a re-export of the same pattern bumps the version |
| `category` | `interaction`, `self_promo`, `cross_promo`, `intro` or `outro` (`recap` is not accepted yet; `sponsor` never) |
| `short_ask` | `true` for a short host ask (interaction or self_promo only): smaller core, near-exact matching |
| `bucket`, `key_kind` | 10-bit bucket of the show key; whether the key is the `podcast_guid` or the feed-URL guid |
| `kdf`, `shingle_k`, `hash_bits` | scrypt n=32768 r=8 p=1; word 3-shingles; 24-bit hashes |
| `core`, `head`, `tail` | keyed shingle hashes (12-400, short asks 4-40) and the first and last 6 (short asks 3) in spoken order |
| `duration`, `position` | p10/p50/p90 seconds rounded to 0.5; where the segment sits (`start`, `end`, `break`, `any`) |
| `evidence` | confirms and episodes (capped at 10) and the span in whole weeks |

Pull requests touching this directory run `python -m src.tools.show_segment_validator`: the schema, sizes
per shape, the allowed categories, no plaintext field and no `@` anywhere, and at most 6 overlapping
entries per bucket and category.
