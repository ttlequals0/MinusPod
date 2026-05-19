# Podcasting 2.0 in MinusPod

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

There is nothing to configure. This behavior is automatic for every
feed MinusPod serves.

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
| [`podcast:txt`](https://podcasting2.org/docs/podcast-namespace/tags/txt) (general, no `verify` purpose) | Free-form metadata, harmless to carry. |

### Regenerated for the processed audio

MinusPod already produces these for the cut file. The upstream
versions are discarded because their timestamps point into the
original, uncut audio:

| Tag | Why it is regenerated |
|---|---|
| [`podcast:transcript`](https://podcasting2.org/docs/podcast-namespace/tags/transcript) | MinusPod generates a transcript aligned to the processed audio. An upstream transcript would be offset by the length of every removed ad. |
| [`podcast:chapters`](https://podcasting2.org/docs/podcast-namespace/tags/chapters) | Same reason. Chapter markers from the original feed land in the wrong place after cutting. |
| `itunes:duration` | Recomputed from the processed file's actual length. |

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
