# YouTube Feeds

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [What a YouTube feed is](#what-a-youtube-feed-is)
- [What decides the cuts](#what-decides-the-cuts)
- [Enabling the feature](#enabling-the-feature)
- [Adding a playlist or channel](#adding-a-playlist-or-channel)
- [How processing works](#how-processing-works)
- [Transcripts and chapters](#transcripts-and-chapters)
- [Shorts and duration filters](#shorts-and-duration-filters)
- [Private and deleted episodes](#private-and-deleted-episodes)
- [SponsorBlock categories](#sponsorblock-categories)
- [Submitting confirmed ads to SponsorBlock](#submitting-confirmed-ads-to-sponsorblock)
- [Cookies](#cookies)
- [yt-dlp](#yt-dlp)
- [Per-feed overrides](#per-feed-overrides)
- [OPML export](#opml-export)
- [Licensing](#licensing)
- [Troubleshooting](#troubleshooting)
- [Reference](#reference)

Adding a show by name, SponsorBlock marks on an episode, the rating, submitting reviewed segments, the status bar during a SponsorBlock outage and the settings. Portrait cut and the file itself in [Videos](videos/README.md).

https://github.com/user-attachments/assets/5ba14213-c2f7-4bec-a70d-b7d722a7209d

## What a YouTube feed is

A YouTube feed is a podcast feed MinusPod builds from a YouTube playlist or channel. MinusPod lists the videos, downloads their audio with yt-dlp, removes sponsor segments, and serves the result as RSS like any other feed. Use it for a show that only exists on YouTube.

YouTube's terms of service restrict downloading. This feature is for personal use with content you are allowed to keep; check the terms for your situation before enabling it. It is off by default.

Like a [local feed](local-feeds.md), there is no podcast RSS upstream. MinusPod lists videos itself, on the normal feed refresh schedule, and serves every discovered video as an episode.

## What decides the cuts

Cuts on a YouTube feed come from SponsorBlock, a community database of timestamps for sponsor reads, self-promotion, intros and similar spans. Read this before enabling the feature: the trust model differs from the LLM pipeline.

- Community marks drive the cuts. By default MinusPod cuts where SponsorBlock says to cut, without asking the LLM.
- Learned patterns (audio fingerprints and text patterns) always run, including when marks exist. Cuts are the marks plus any validated pattern hits, and MinusPod learns new patterns from the marks it cuts.
- The LLM runs only when an episode has no marks or Pass 1 detection is on. **Full reprocess** and **Re-detect ads** on an episode force pass 1 for that run.
- Marks keep arriving after an episode is published. During the watch window, a changed set of marks triggers an automatic recut from the retained original audio.
- Marks are filtered before use. A mark with a negative vote total is dropped unless a SponsorBlock moderator locked it. Marks of unknown category are dropped. If a mark declares a video duration that differs from the downloaded audio by more than 2 seconds, all marks for that video are ignored. A mark whose end is not after its start is dropped.
- A short watch ceiling limits how long late marks can change an episode.

To turn the behavior down, use any of these:

| Goal | Setting |
|---|---|
| Stop accepting late marks sooner | Lower **Watch ceiling (days)** in Settings > YouTube (global only) |
| Have the LLM review every episode as well | Turn on **Pass 1 detection** for the feed |
| Skip the wait for marks | Set **SponsorBlock probe attempts** to 0 with the LLM fallback on; episodes with no marks at the first check go straight to the LLM |
| Never trust marks for one category | Set that category's segment action to Keep in Settings > Segment categories, or per feed |

## Enabling the feature

Open Settings > YouTube.

1. Turn on **Enable YouTube feeds**. Until this is on, the Add feed page offers no YouTube lookup, and the watcher, feed refreshes, artwork lookups and playback processing do nothing for YouTube feeds.
2. Optionally set a **YouTube Data API key**. Without one, MinusPod resolves sources and lists videos with yt-dlp. With one it uses the Data API, which is faster and returns exact publish dates. If the key's daily quota runs out, MinusPod falls back to yt-dlp until the quota resets.
3. Choose **Test key** to check a key. It calls the Data API once against a public channel and reports the result.

To create a key, open the Google Cloud console, create a project, enable the YouTube Data API v3, and create an API key. Restrict the key to that one API. MinusPod sends it in the `X-Goog-Api-Key` header, never in a URL, and stores it encrypted. The key is write-only: the API reports `apiKeyConfigured` and never returns the value. Saving a key requires secret encryption to be available.

Without a key, yt-dlp listings carry no episode descriptions and only a date for the publish time. MinusPod fills the description and exact publish time from the video's metadata when the episode is downloaded, and a background backfill does the same for up to 10 episodes per refresh pass without downloading. With a key, the Data API supplies descriptions when episodes are discovered.

## Adding a playlist or channel

On the Add feed page, paste a YouTube address and choose **Look up**. The card shows the title, artwork, video count and any podcast directory matches that offer the same show as an RSS feed. If a directory match exists, prefer the RSS feed: it is the publisher's own audio. To use the YouTube source anyway, choose **Add YouTube feed**. The page also keeps the normal RSS form under **Or add by RSS feed**.

Typing a show name instead of an address searches YouTube. Matching playlists and channels are listed under the directory results. The search reads the Podcasts tab of up to three channels from the results. A playlist is marked as a podcast when that tab lists it, and marked playlists rank higher. Choosing one opens the same lookup card, including any RSS directory matches. Without a Data API key a search usually takes a few seconds.

Accepted forms:

| Input | Example |
|---|---|
| Playlist URL | `https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Any watch URL carrying a playlist | `https://www.youtube.com/watch?v=VIDEO_ID&list=PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Bare playlist id | `PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (also `UU`, `UULF`, `UUSH`, `UULV`, `OLAK5uy_`, `RD`, `FL` prefixes) |
| Channel URL | `https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx` |
| Handle | `https://www.youtube.com/@example-channel` or `@example-channel` |
| Single video | `https://www.youtube.com/watch?v=VIDEO_ID`, `https://youtu.be/VIDEO_ID`, or a `/live/` or `/shorts/` URL |
| Legacy names | `https://www.youtube.com/c/example-channel`, `https://www.youtube.com/user/example-channel` |

A video URL creates a feed with that one episode, and the lookup card shows the source kind as Video. A channel feed lists long-form uploads by default (see [Shorts and duration filters](#shorts-and-duration-filters)). Adding a source that already exists is rejected, and the lookup reports the existing feed. A private or missing playlist returns "Playlist not found or private".

The feed's first listing runs in the background after the add request returns, so the new feed page can be empty for a moment. Later listings run on the normal feed refresh schedule.

MinusPod sets the feed author from the channel name when you add a feed. You can change the author, the explicit flag and the categories with `PATCH /api/v1/feeds/<slug>`.

A playlist feed reads the whole playlist, sorts it by publish date, and keeps the newest videos up to the feed's episode limit, so an oldest-first playlist still shows its latest videos. With a Data API key this costs one quota unit per 50 playlist items on each refresh. A channel feed reads the uploads list, which is already newest first, and stops at the limit.

## How processing works

A YouTube episode does not process the moment it is discovered. MinusPod gives the SponsorBlock community time to mark it first.

1. **Delay.** After discovery the episode waits **Processing delay (minutes)** (default 60, range 0 to 1440; 0 means as soon as discovered).
2. **Probe loop.** MinusPod asks SponsorBlock for marks up to **SponsorBlock probe attempts** times (default 6, range 0 to 48), **Probe interval (minutes)** apart (default 30, range 5 to 1440). The first attempt that finds marks queues the episode.
3. **No marks after the last probe.** If **Fall back to the LLM pipeline when no marks appear** is on (default), the episode is queued for the normal LLM pipeline. If it is off, the episode enters the watch window and keeps waiting.
4. **Watch window.** Every **Watch interval (hours)** (default 6, range 1 to 48) MinusPod checks again. When marks appear the episode is queued. If none appear by the **Watch ceiling (days)** (default 7, range 1 to 60), measured from the publish date, the episode is processed with the LLM pipeline.
5. **After processing.** For the rest of the watch window MinusPod keeps checking. If the set of marks changes and the original audio is still retained, the episode is recut automatically.
6. **Pass 1 detection.** A per-feed switch. When on, the LLM detector runs in addition to SponsorBlock even when marks exist, and both sets of segments are cut.

A failed SponsorBlock lookup is retried at the state's normal interval, capped at 24 hours. If a queued episode reaches the pipeline and no usable marks are left, or the lookup fails, the run steps aside instead of failing. The episode goes back to waiting and the watcher checks again.

The served feed lists every discovered episode, up to the feed's episode cap, with its upload date. The first play of an unprocessed episode starts processing, and the app retries until the cut is ready. Turn on "Only expose processed episodes" for the feed to list finished episodes only.

Processed YouTube episodes also appear in the combined Recents feed.

Manual **Process** or **Reprocess** on a waiting episode skips the wait. With no marks yet it runs the LLM pipeline; with marks it uses them like an automatic run.

### Episode states

The episode list and episode page show one of these lines.

| State | Text in the UI | Meaning |
|---|---|---|
| waiting | `Waiting for SponsorBlock, attempt N of M, next check in 30 min` | Delay or probe loop; the first check has not found marks |
| probing | same text as waiting | A probe is under way or marks were found and the episode is queued |
| watching | `Watching SponsorBlock, next check in 6 h` | Probes finished with fallback off; checking every watch interval |
| done | `SponsorBlock: N segments` | Processed from SponsorBlock marks; still watching for changes until the ceiling |
| exhausted | `SponsorBlock timed out, processed with LLM` | The watch ceiling passed with no marks |
| llm | `No SponsorBlock marks, processed with LLM` | The probe loop found nothing and the LLM fallback ran |
| exhausted, no captions | `No marks and no captions, served unmodified` | No marks and no captions with transcription off, after the watch ceiling or a manual reprocess; the audio is served as published |

Three suffixes can follow: `, marked whole video as sponsored` when SponsorBlock flags the entire video (see [SponsorBlock categories](#sponsorblock-categories)), `, no captions (transcription off)` when the video had no captions and **Transcribe when captions are missing** is off, and `, last check failed` when the most recent lookup errored.

## Transcripts and chapters

- **Captions first.** When **Use YouTube captions as the transcript** is on (global default, per-feed override), yt-dlp downloads captions with the audio. MinusPod prefers the creator's track, then the auto-generated track, in the feed's language (the transcription language override, `en` if unset). With the feed language set to auto, the original-language auto captions are used. The captions become the transcript and Whisper is skipped. Speaker-change markers (`>>`) and bracketed sound cues such as `[Music]` are removed from the caption text.
- **Whisper when none.** With the captions toggle off, the normal transcription backend runs. With it on and a video that has no captions, the backend (Whisper or Parakeet, whichever you configured) runs only if **Transcribe when captions are missing** is on. That setting is global, default off, with a per-feed override. When it is off and the video has SponsorBlock marks, the cuts come from the marks alone and the LLM is skipped, even with **pass 1 detection**, because there is no transcript. With no marks, the episode keeps waiting for them and does not fall back to the LLM. Once the watch ceiling passes, or when you reprocess it, the episode is served unmodified, since there is no transcript to detect ads in.
- **Creator chapters.** yt-dlp embeds the video's chapters in the audio (`--embed-chapters`). MinusPod reuses them, so the LLM chapter generation call is skipped for those episodes.
- **Artwork.** Feed artwork comes from the source's thumbnail. You can upload your own or refresh it from the feed's YouTube panel. Episode thumbnails are cached when an episode is processed and served as per-episode artwork.

## Shorts and duration filters

- Playlist feeds skip videos of 180 seconds or less by default.
- Channel feeds read the channel's long-form uploads list (`UULF` in place of `UU`), so Shorts never appear.
- Turning on **Shorts** for a feed (API: `youtubeIncludeShorts`) includes them. For a channel it switches to the full uploads list.
- Live and upcoming videos are never listed.

## Private and deleted episodes

Playlist listings hide private and deleted episodes by default. yt-dlp reports them as an entry with only an id or a placeholder title such as `[Private video]`, and the download fails. Turn **Hide private and deleted episodes** off (API: `hidePrivate` globally, `ytHidePrivateOverride` per feed) when the cookies file belongs to an account that can watch them. They are then listed as Private episode (or Deleted episode) until a cookie account that can see them supplies the real title. The Data API listing never returns these entries.

The feed's normal minimum and maximum episode duration filters still apply on top of this. See [Configuration > Episode duration filters](configuration.md#episode-duration-filters).

## SponsorBlock categories

SponsorBlock categories map to MinusPod segment categories:

| SponsorBlock | MinusPod category |
|---|---|
| sponsor | sponsor |
| selfpromo | self_promo |
| interaction | interaction |
| intro | intro |
| outro | outro |
| preview | recap |

What happens to a mark is decided by the per-category segment action in Settings > Segment categories, with a per-feed override: Remove, Beep, Keep or Mark. A SponsorBlock `mute` mark is beeped when its category is set to cut, and left alone when the category is set to keep or mark. The `full` action (the whole video is a sponsored segment) is never cut; it only adds the note shown in the episode state.

When a SponsorBlock lookup fails, the watcher marks the API as down and checks it on every pass until it answers again. While it is unreachable, YouTube episodes waiting on it are held without counting attempts, submissions are refused, and the status bar shows SponsorBlock as unreachable. Other feeds keep processing, and the held episodes resume on the next pass once it is back.

## Submitting confirmed ads to SponsorBlock

MinusPod can help you send the ads it cut back to SponsorBlock, but only by hand. SponsorBlock's rules forbid automated submissions, so MinusPod never submits on its own. A person reviews each segment and chooses to send it.

Turn on **Allow SponsorBlock submissions** in Settings > YouTube and enter your SponsorBlock user ID. It is off by default. The ID is stored encrypted and hidden by default; **Show** reveals it and **Copy** copies it. Each submission carries the reputation of that ID, so use an account you are willing to be judged by.

You do not need the SponsorBlock browser extension. Choose **Generate** beside the field and MinusPod makes a random ID and stores it. Use **Show** and **Copy** any time to read the stored ID. Pasting it into the extension under Options, Import lets you see your submissions there. Generate only works while no ID is stored, and a generated ID cannot be cleared or replaced. **Clear user ID** only applies to an ID you pasted yourself. A new ID has no reputation, so its first segments stay hidden from other users until they are upvoted.

On the episode page of a YouTube episode, open the SponsorBlock review. Every box starts unchecked. Each segment has a play button so you can listen to the span before ticking it. Tick the segments you have checked against the video, then choose **Submit**. A request sends at most 20 segments. Submission waits until SponsorBlock has confirmed the video has no community marks.

A segment is listed when all of these hold:

- The episode belongs to a YouTube feed.
- SponsorBlock had no community marks for the video.
- The segment is a sponsor or self promotion cut.
- The segment has evidence beyond the LLM, such as a verified span, a reviewer confirmation, a fingerprint or pattern match, or your own confirmation.
- The segment is not held for review.

Each row shows its evidence. Giving the episode 5 stars confirms every cut segment, so they all qualify afterwards.

MinusPod records the outcome of each segment on the episode: submitted, duplicate, rejected, rate limited or error. Submitted and duplicate segments are shown greyed out and cannot be sent again. A rejected, rate limited or failed segment can be sent again. The user ID is never logged.

Read the SponsorBlock guidelines on segment timing and categories before you submit.

## Cookies

Some videos need a signed-in session, for example to pass a bot check or an age gate. Paste a Netscape-format `cookies.txt` into Settings > YouTube > Cookies and choose **Save cookies**.

- Export it from a browser with a cookies.txt extension, from a throwaway account. Do not use an account you care about, because automated downloads can get an account restricted.
- The file needs 7 tab-separated fields per line. A `# Netscape HTTP Cookie File` header is optional.
- Cookies expire. When downloads start failing with a sign-in error again, export a fresh file.
- **Clear cookies** deletes the file.
- The file is stored on the data volume with mode 0600, only yt-dlp reads it, and its content is never logged.

## yt-dlp

yt-dlp changes often because YouTube does. MinusPod keeps up in layers.

- **Baked floor.** Both images ship a pinned yt-dlp (the version in `ytdlp-version.txt`) and the Deno runtime it needs for YouTube.
- **Data-volume copy.** On first use MinusPod copies the baked binary into the data volume and runs that copy. A newer baked version replaces an older copy after an image upgrade. If the copy cannot execute, MinusPod uses the image copy and shows an error under Settings > YouTube.
- **Daily update.** With **Update yt-dlp daily** on (default), MinusPod updates the data-volume copy once at start and then every 24 hours.
- **Update now.** Runs an update immediately; limited to 6 per hour.
- **Outdated extractor.** When a download fails with a message such as "Unable to extract" or "nsig extraction failed", MinusPod runs one update and retries on the next attempt. The episode shows the error until then.

Settings > YouTube shows the running version, the version the image ships, and the last check time and error.

**Reset to defaults** at the bottom of Settings > YouTube restores the processing settings, daily update toggle, proxy URL and extra arguments. The feature switch, Data API key and cookies file are kept.

**Proxy URL** (API: `proxyUrl`) routes every yt-dlp call and every YouTube Data API request through a proxy. It accepts `http`, `https`, `socks4`, `socks5` and `socks5h` URLs, for example `socks5h://proxy:1080`, up to 500 characters. The API returns any password in the URL as `***`, and saving that masked value leaves the stored password unchanged. Self-updates ignore it. Set the proxy here rather than in the extra arguments, which reject URL values.

**Extra yt-dlp arguments** (API: `ytdlpExtraArgs`) is split like a shell command and added before MinusPod's own options on every yt-dlp call: listings, metadata lookups, caption fetches and downloads. Self-updates ignore it. MinusPod's options come last, so output format and paths stay fixed. Example: `--limit-rate 2M`. Options that run or load other programs, change where files are written, read local files, alter the JSON output, update the binary or carry account passwords (use the cookies file instead) are rejected, and abbreviated or bundled short options are rejected too, so use full long option names. Up to 1000 characters.

## Per-feed overrides

The feed page has a YouTube section. Each field inherits the global setting until changed.

| Field | API field | Effect |
|---|---|---|
| LLM fallback | `ytLlmFallbackOverride` | Run the LLM pipeline when no marks appear |
| Pass 1 detection | `ytLlmOnTopOverride` | Run the LLM detector in addition to SponsorBlock marks and cut both |
| Captions | `ytCaptionsOverride` | Use YouTube captions as the transcript |
| Hide private and deleted episodes | `ytHidePrivateOverride` | Drop playlist entries the listing cannot see |
| Transcribe when captions are missing | `ytTranscribeOverride` | Run the transcription backend for videos without captions |
| Delay | `ytProcessDelayMinutesOverride` | Processing delay in minutes |
| Probe attempts | `ytProbeAttemptsOverride` | SponsorBlock lookup attempts |
| Probe interval | `ytProbeIntervalMinutesOverride` | Minutes between attempts |
| Shorts | `youtubeIncludeShorts` | Include videos of 180 seconds or less |

The watch interval and watch ceiling are global only.

## OPML export

YouTube feeds are skipped in `mode=original` because the source is not an RSS URL. They are included in `mode=modified` with the MinusPod-served feed URL.

## Licensing

SponsorBlock data is licensed CC BY-NC-SA 4.0. Markers that come from it are tagged `sponsorblock`, and they never enter the community pattern export or pattern learning. MinusPod identifies itself to the SponsorBlock API with a `MinusPod/<version>` client name and looks marks up by a 4-character hash prefix of the video id, so the service does not see which video you asked about.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Sign in to confirm you're not a bot" | YouTube wants a session. Add cookies from a throwaway account ([Cookies](#cookies)) |
| "No JS runtime" | The Deno binary is missing, usually from a custom or old image. Pull or rebuild the current image |
| `quotaExceeded` | The Data API key hit its daily quota. MinusPod falls back to yt-dlp for listing; wait for the reset or raise the quota |
| "Playlist not found or private" | The playlist id is wrong, or the playlist is private |
| "yt-dlp finished without producing an mp3" | ffmpeg failed to convert the download. Check the container log for the ffmpeg error and confirm the image has ffmpeg |
| Downloads worked, then began failing | Cookies went stale, or yt-dlp is outdated. Try **Update now**, then fresh cookies |
| Episode stays in "Waiting for SponsorBlock" | Working as designed. Raise **Watch ceiling** only if you want to wait longer; process it by hand to use the LLM now |

## Reference

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/settings/youtube` | Global YouTube settings, yt-dlp status, cookie status and setting bounds |
| `PUT /api/v1/settings/youtube` | Update settings; `apiKey` is write-only and null clears it |
| `POST /api/v1/youtube/preview` | Resolve a URL to a source plus RSS directory matches (20 per minute) |
| `POST /api/v1/youtube/test-key` | Check a Data API key; omit `apiKey` to test the stored one (10 per minute) |
| `POST /api/v1/youtube/cookies` | Save a Netscape cookies file |
| `DELETE /api/v1/youtube/cookies` | Remove the cookies file |
| `POST /api/v1/youtube/ytdlp/update` | Update yt-dlp now (6 per hour) |
| `POST /api/v1/feeds` | With `feedType: "youtube"`, `sourceKind`, `sourceId` and `title`, create a feed |

Episode responses on a YouTube feed carry a `sponsorBlock` object with the state, attempt count, next check time, segment count, note and last error. The settings are listed in [Configuration > YouTube](configuration.md#youtube). The full schema is in [`openapi.yaml`](../openapi.yaml).

---

[< Docs index](README.md) | [Project README](../README.md)
