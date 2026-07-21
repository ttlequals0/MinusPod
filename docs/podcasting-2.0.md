# Podcasting 2.0 in MinusPod

[< Docs index](README.md) | [Project README](../README.md)

---

MinusPod sits between the original podcast feed and your player. It
downloads each episode, removes ad segments, re-encodes the audio, and
serves a modified RSS feed pointing at the processed files.

That position has consequences for the Podcast Namespace (the set of
`podcast:` tags often called "Podcasting 2.0"). MinusPod is not the
podcast's publisher. It is a proxy that rewrites someone else's feed.
Some namespace tags survive that rewrite unchanged. Some describe the
original audio and become wrong the moment an ad is cut. A few would
actively break playback if passed through. This document explains
exactly what MinusPod emits, what it deliberately drops, and why.

Almost none of this is configurable. The one exception is chapters:
each feed can choose how its `podcast:chapters` tag is populated (see
"Chapter modes" below). Everything else in this document is automatic
for every feed MinusPod serves.

## The rule

Every Podcast Namespace tag falls into one of four buckets:

1. **Pass through**: the tag is still true after ad removal, so the
   upstream value is copied into the served feed unchanged.
2. **Regenerate**: the tag describes the audio, so MinusPod produces
   its own correct version for the processed file.
3. **Strip**: the tag asserts something about the original audio that
   is false for the re-cut file, so emitting it would be a lie or
   would break players. It is removed.
4. **Always emit**: MinusPod adds the tag regardless of the upstream
   feed.

"Strip" is not a feature gap. Passing these tags through would make
the feed describe audio that no longer exists.

## What MinusPod supports

### Always emitted

| Tag | Behavior |
|---|---|
| [`podcast:guid`](https://podcasting2.org/docs/podcast-namespace/tags/guid) | MinusPod mints its own feed GUID. See "Feed identity" below. |
| [`podcast:txt`](https://podcasting2.org/docs/podcast-namespace/tags/txt) (`purpose="ai-content"`) | Always set to `true`. MinusPod algorithmically re-cuts the audio, and the served feed discloses that. Any upstream `ai-content` value is replaced, because once MinusPod has processed the file the upstream claim is no longer accurate. |

### Passed through from the original feed

These remain accurate after ad removal and are copied verbatim:

| Tag | Why it survives |
|---|---|
| [`podcast:funding`](https://podcasting2.org/docs/podcast-namespace/tags/funding) | The creator's donation links are unaffected by ad removal. |
| [`podcast:locked`](https://podcasting2.org/docs/podcast-namespace/tags/locked) | Reflects the creator's intent about feed re-import. If the original feed has no `locked` tag, MinusPod emits `locked` as `yes`, because a private re-feed should not be imported into public directories. |
| [`podcast:podroll`](https://podcasting2.org/docs/podcast-namespace/tags/podroll) | Show recommendations remain valid and useful for discovery. |
| [`podcast:value`](https://podcasting2.org/docs/podcast-namespace/tags/value), [`podcast:valueRecipient`](https://podcasting2.org/docs/podcast-namespace/tags/value-recipient), [`podcast:valueTimeSplit`](https://podcasting2.org/docs/podcast-namespace/tags/value-time-split) | Value-for-value payment information is passed through untouched so listener support still reaches the original creator. MinusPod does not insert itself into the payment split. |
| [`podcast:license`](https://podcasting2.org/docs/podcast-namespace/tags/license) | Content license is metadata, not tied to the audio timeline. |
| [`podcast:medium`](https://podcasting2.org/docs/podcast-namespace/tags/medium) | Describes the kind of content, unaffected. |
| [`podcast:person`](https://podcasting2.org/docs/podcast-namespace/tags/person) | Host and guest credits are unaffected by cutting. |
| [`podcast:updateFrequency`](https://podcasting2.org/docs/podcast-namespace/tags/update-frequency) | The publishing schedule does not change. |
| [`podcast:season`](https://podcasting2.org/docs/podcast-namespace/tags/season), [`podcast:episode`](https://podcasting2.org/docs/podcast-namespace/tags/episode) | Structural numbering, no timeline dependency. |
| [`podcast:trailer`](https://podcasting2.org/docs/podcast-namespace/tags/trailer) | A trailer is its own separate file, not an offset into the episode. |
| [`podcast:image`](https://podcasting2.org/docs/podcast-namespace/tags/image) | Artwork sources improve display and are unaffected. The deprecated plural `podcast:images`, if present upstream, is also passed through untouched. |
| [`podcast:socialInteract`](https://podcasting2.org/docs/podcast-namespace/tags/social-interact) | Comment and discussion links are unaffected. |
| [`podcast:block`](https://podcasting2.org/docs/podcast-namespace/tags/block) | The publisher's directory-block hints. Multiple instances are allowed, optionally scoped to one platform with an `id` attribute (e.g. `id="apple"`). MinusPod copies each one verbatim. Stripping these would silently expose the re-feed in directories the publisher chose to keep it out of. |
| [`podcast:complete`](https://podcasting2.org/docs/podcast-namespace/tags/complete) | Boolean for "no more episodes are coming". Applies to the show, not the audio timeline; ad removal does not change whether the run is over. |
| [`podcast:txt`](https://podcasting2.org/docs/podcast-namespace/tags/txt) (general, no `verify` purpose) | Free-form metadata, harmless to carry. |

### Regenerated for the processed audio

MinusPod already produces these for the cut file. The upstream
versions are discarded because their timestamps point into the
original, uncut audio, and because subscribers to a MinusPod feed
must never be sent to a publisher URL: the proxy serves everything
or nothing. Until MinusPod finishes processing an episode, the served
feed carries no transcript or chapter URL for it; the audio enclosure
returns 503 with a JIT-triggered processing job behind it.

| Tag | Why it is regenerated |
|---|---|
| [`podcast:transcript`](https://podcasting2.org/docs/podcast-namespace/tags/transcript) | MinusPod generates a transcript aligned to the processed audio. An upstream transcript would be offset by the length of every removed ad, and would also point subscribers at the publisher's CDN. |
| [`podcast:chapters`](https://podcasting2.org/docs/podcast-namespace/tags/chapters) | MinusPod always serves its own chapters JSON at its own URL, never the upstream one, because the upstream timestamps point into the original, uncut audio. What that JSON contains depends on the per-feed chapter mode; see "Chapter modes" below. |
| `itunes:duration` | Recomputed from the processed file's actual length. |

### Chapter modes

Each feed has a chapter mode, set on its Feed Settings page: **Auto**
(the default), **Always generate**, or **Off**.

**Auto** preserves the podcast's own chapters when enough of them
survive the cut. The ffmpeg cut step already remaps any embedded ID3
chapter markers onto the cut audio's timeline, so once an episode is
processed MinusPod probes the finished file for what made it through.
When at least two survive, they become the served `podcast:chapters`
JSON: the publisher's own titles and boundaries, shifted to account
for the removed ads. With fewer than two survivors, or no embedded
chapters at all, MinusPod falls back to AI-generated chapters. If the probe
itself fails (for example a transient ffprobe error), MinusPod treats
that as unknown, not as "no chapters", and skips the chapter step for
that run rather than risk overwriting the ID3 frames the cut step
already wrote correctly.

**Always generate** skips the probe and always produces AI chapters,
matching MinusPod's behavior before this setting existed.

**Off** stops chapter work for episodes processed from then on: no
probing, no generation, and nothing saved, so those episodes carry no
`podcast:chapters` tag. Episodes processed before the switch keep the
chapters they already have until they are reprocessed.

Auto is the default because it keeps the publisher's chapters
whenever they hold up across the cut; replacing accurate publisher
chapters with generated ones is what prompted issue #560.

## What MinusPod does not support, and why

### Deliberately stripped

These describe the original audio's timeline or bytes. After ad
removal they are false. Two of them can make a conformant player
refuse to play the episode.

| Tag | Why it is removed |
|---|---|
| [`podcast:soundbite`](https://podcasting2.org/docs/podcast-namespace/tags/soundbite) | `startTime` and `duration` point into the original timeline. After cutting, that offset is wrong, so a "highlight" would play the wrong audio, possibly mid-sentence or inside a removed ad. |
| [`podcast:integrity`](https://podcasting2.org/docs/podcast-namespace/tags/integrity) | A cryptographic hash of the original audio bytes. The processed file will never match it. A player that verifies integrity will reject the episode outright. |
| [`podcast:alternateEnclosure`](https://podcasting2.org/docs/podcast-namespace/tags/alternate-enclosure) | Points at other copies of the original, un-stripped audio. Following it bypasses ad removal entirely. |
| [`podcast:source`](https://podcasting2.org/docs/podcast-namespace/tags/source) | Describes sources of the original audio, not the processed file MinusPod serves. |
| [`podcast:liveItem`](https://podcasting2.org/docs/podcast-namespace/tags/live-item) | A live stream. By the time MinusPod has downloaded and processed recorded audio, the live event is over. It has no meaning in a re-cut feed. |
| [`podcast:txt`](https://podcasting2.org/docs/podcast-namespace/tags/txt) (`purpose="verify"` or `purpose="applepodcastsverify"`) | An ownership-verification token a hosting platform issued to the original publisher. It proves nothing about a MinusPod re-feed, and carrying it forward leaks the original owner's token into a feed they don't control. |

### Not supported: Podping

`podcast:podping` signals that a feed sends Podping notifications when
it updates. MinusPod does not implement Podping and does not emit this
tag.

This is a deliberate decision, not a missing feature. Podping works by
writing the feed URL to the Hive blockchain, a public, permanent,
append-only ledger that anyone can watch. The entire purpose of
Podping is to broadcast feed updates to every aggregator and directory.
MinusPod serves private, ad-stripped re-feeds of podcasts you do not
own. Publishing those URLs to a permanent public record, and inviting
the whole podcast ecosystem to crawl them, is the opposite of what a
private re-feed is for. There is no private mode for Podping. MinusPod
will not add it.

## Feed identity

MinusPod mints its own `podcast:guid` rather than reusing the original
feed's GUID.

The GUID is the stable identifier other tools use to recognize a feed.
A MinusPod feed is a modified derivative served at a different URL. If
it claimed the original's GUID, aggregators could treat the two feeds
as the same feed, which they are not.

The GUID is computed the way the
[Podcast Namespace `guid` spec](https://podcasting2.org/docs/podcast-namespace/tags/guid)
specifies: a deterministic UUIDv5 over the served feed URL, using the
namespace's fixed constant. The same feed URL always produces the same
GUID, so the identity is stable for as long as the feed is served at
the same address. If you move MinusPod to a different domain, the
served URL changes and so does the GUID, which is correct, because at
that point it is genuinely a feed at a new address.

## Upstream namespace URIs

The Podcast Namespace spec treats several xmlns URIs as equivalent, and
real-world feeds use them interchangeably. The reference `pc20.xml` feed,
for example, declares the GitHub-blob form rather than the
`podcastindex.org` form. MinusPod accepts all of these on parse:

- `https://podcastindex.org/namespace/1.0`
- `https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md`
- `https://github.com/Podcastindex-org/podcast-namespace/blob/master/docs/1.0.md`
- The `http://` variants of each

The served feed always declares the canonical `https://podcastindex.org/namespace/1.0`
URI on its `<rss>` root, no matter which form the upstream feed used.
Passthrough tags are re-emitted under the canonical `podcast:` prefix.

## Compatibility with older players

Every tag MinusPod adds is in the `podcast:` namespace. A player that
does not understand a tag ignores it; the standard RSS and `itunes:`
elements it relies on are untouched. A basic podcast app sees a normal
feed and plays the audio. Podcasting 2.0 apps see the additional tags.
Nothing about this support breaks older clients.

## Summary

MinusPod aims to be as Podcasting 2.0 compliant as a re-cutting proxy
honestly can be: it carries forward everything that stays true, it
regenerates what it can produce correctly for the processed audio, it
discloses that the audio was modified, and it refuses to emit tags
that would describe audio that no longer exists. Being a good
namespace citizen here includes not lying in the feed.

## References

External Podcasting 2.0 documentation:

- [Podcasting 2.0 documentation home](https://podcasting2.org/docs)
- [Podcast Namespace introduction](https://podcasting2.org/docs/podcast-namespace)
- [RSS 2.0 namespace declaration](https://podcasting2.org/docs/podcast-namespace/1.0)
- [Full tag reference](https://podcasting2.org/docs/podcast-namespace/tags/transcript)
  (sidebar lists every tag)
- [Podcast Namespace source repository](https://github.com/Podcastindex-org/podcast-namespace)
- [Podcasting 2.0 apps and players](https://podcasting2.org/apps)
- [Publishing tools and hosts](https://podcasting2.org/publishing-tools)

Per-tag specifications are linked inline from the tables above.

---

[< Docs index](README.md) | [Project README](../README.md)
