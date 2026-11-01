# Videos

[< Docs index](../README.md) | [Project README](../../README.md)

Short videos about MinusPod: a tour, the pipeline, and one per feature that
rewards a closer look. Each comes in a portrait cut for TikTok and other 9:16
feeds and a landscape cut for YouTube, a README or a talk. Portrait is 1080x1920
and landscape is 1920x1080, both 30 fps H.264 with no audio track, so a caption
or music can be added in the posting app. The landscape cuts also play inline in the docs that cover each feature; see [Embedding in the docs](#embedding-in-the-docs).

| Video | Portrait | Landscape | Length | What it shows |
|---|---|---|---|---|
| UI tour | [minuspod-demo.mp4](minuspod-demo.mp4) | [minuspod-demo-landscape.mp4](minuspod-demo-landscape.mp4) | 104 s | Dashboard, processing queue, detected ads, the waveform editor, review, patterns, search, stats, settings and themes, then YouTube feeds: adding a show by name, SponsorBlock marks, the episode rating and the YouTube settings. Recorded from a running instance. |
| How it works | [minuspod-how-it-works.mp4](minuspod-how-it-works.mp4) | [minuspod-how-it-works-landscape.mp4](minuspod-how-it-works-landscape.mp4) | 107 s | One episode across all eleven pipeline stages: download, transcribe, second download and audio analysis, detect (free matches, then the model), validate, boundary review, cut, verify pass, transcript and chapters, publish. Then the learning loop and what happens when something fails. Follows [How It Works](../how-it-works.md) and [Episode Processing Workflows](../workflows.md). |
| Review queue | [minuspod-review-queue.mp4](minuspod-review-queue.mp4) | [minuspod-review-queue-landscape.mp4](minuspod-review-queue-landscape.mp4) | 42 s | A held detection from the queue to the waveform editor: play the span, confirm it, watch the recut, see it on the record. Recorded from a running instance. |
| Audio cues | [minuspod-audio-cues.mp4](minuspod-audio-cues.mp4) | [minuspod-audio-cues-landscape.mp4](minuspod-audio-cues-landscape.mp4) | 43 s | Find a show's ad-break chime on one episode, templates and their health on the feed, the cross-episode scan, cuts that snapped to a cue, and the settings behind it. Recorded. |
| Processing modes | [minuspod-processing-modes.mp4](minuspod-processing-modes.mp4) | [minuspod-processing-modes-landscape.mp4](minuspod-processing-modes-landscape.mp4) | 73 s | One episode under each of the five modes, then segment categories and the remove, beep, keep and mark actions. Animated, source in [processing-modes/](processing-modes/). |
| Local feeds | [minuspod-local-feeds.mp4](minuspod-local-feeds.mp4) | [minuspod-local-feeds-landscape.mp4](minuspod-local-feeds-landscape.mp4) | 59 s | Create a feed from your own files, upload an episode, run it through the pipeline, and find it next to your subscriptions. Recorded. |
| Providers and cost | [minuspod-providers.mp4](minuspod-providers.mp4) | [minuspod-providers-landscape.mp4](minuspod-providers-landscape.mp4) | 48 s | Two provider slots, a model per stage, failover, and the spend ledger from provider down to a single episode. Recorded. |
| Podcasting 2.0 | [minuspod-podcasting-2.mp4](minuspod-podcasting-2.mp4) | [minuspod-podcasting-2-landscape.mp4](minuspod-podcasting-2-landscape.mp4) | 37 s | Chapters and transcript for the cut audio, the served feed's namespace tags, and the chapter settings. Recorded, plus one rendered feed excerpt. |
| Patterns | [minuspod-patterns.mp4](minuspod-patterns.mp4) | [minuspod-patterns-landscape.mp4](minuspod-patterns-landscape.mp4) | 70 s | Learned patterns: the list, one created from a confirmed detection, its detail and edit forms, merge suggestions, then Pattern Cleanup from Run now to approve and undo. Recorded. |
| YouTube feeds | [minuspod-youtube.mp4](minuspod-youtube.mp4) | [minuspod-youtube-landscape.mp4](minuspod-youtube-landscape.mp4) | 71 s | Search by show name and preview, the feed page and its YouTube panel, SponsorBlock marks on an episode, rating, submitting reviewed segments, the status bar during a SponsorBlock outage, and the settings with a generated user ID. Rating, submit and generate are answered by in-page mocks. |

## Rebuilding the animated videos

Each animation is a single HTML canvas page, [how-it-works/index.html](how-it-works/index.html)
or [processing-modes/index.html](processing-modes/index.html), holding its
constants and scenes. The drawing helpers, palette, caption layout and the
frame driver they share live in [lib/canvas.js](lib/canvas.js). Every frame is a
pure function of time, so [render.mjs](render.mjs) steps `window.render(t)` at
30 fps, screenshots each frame with Playwright, and ffmpeg encodes the sequence.
The logo comes from `frontend/public/logo-dark.svg` and the colors mirror the
app's dark theme tokens, so the video tracks the app's own look.

```bash
cd docs/videos/how-it-works        # or docs/videos/processing-modes
npm install                      # playwright, once
npx playwright install chromium  # once, if Chromium is not installed yet
npm run frames && npm run encode                      # portrait
npm run frames:landscape && npm run encode:landscape  # landscape
rm -rf frames
```

The scenes are drawn once, in portrait coordinates. The landscape layout keeps
the caption and the footer rail in a left column and scales the scene content
into the right side of the frame. `npm run preview -- --landscape` previews
that layout.

`npm run preview -- 2,25.5,49` writes single frames at those times to
`preview/` for checking a scene without a full render; with no times it
writes the midpoint of every scene. Rendering needs network
access for the Roboto web font. `frames/` and `preview/` are gitignored.

Scenes, captions and timings are the `add(...)` calls at the bottom of each
`index.html`: a duration, a headline, a subline, a draw function of local
time, and what to light up in the footer rail (pipeline stages in one video,
the processing mode in the other). The episode runtime and ad positions are
constants at the top, and every duration label is derived from them. A new
animation is a folder with an `index.html` that loads `../lib/canvas.js`,
defines its scenes and ends with `finish(...)`, plus a copy of
`processing-modes/package.json` with its output names.

## Recording the screen-capture videos

The UI tour and the feature videos are screen recordings of a running
instance, driven by Playwright so every take is the same. The tooling lives in
[recordings/](recordings/):

| File | Role |
|---|---|
| `shoot.mjs` | Opens the instance in Chromium on a virtual X display and records one clip per beat with ffmpeg. |
| `helpers.mjs` | Scrolling, typing, section expanders, a wait for the waveform to draw, and the safeguards below. |
| `devices.json` | Pixel size, scale factor and default X display of the phone, laptop and desktop shapes. |
| `screenshot.mjs` | Headless screenshot of a page after some actions, for planning a beat before recording it. |
| `beats/<video>.mjs` | The beats of one video: a device, a URL, off-camera setup and the on-camera actions. |
| `beats/<video>.json` | The cut: which clip, from when, for how long, with what caption, framed how. |
| `compose.py` | Frames each clip in a phone or laptop shape, adds the caption, joins the beats with crossfades. |
| `targets.example.json` | The instance URL, the feeds and episodes each video uses, and the strings that must never appear. |
| `feedcard.mjs` | Renders a served RSS feed as a code card clip, for the Podcasting 2.0 video. |
| `ocrcheck.py` | OCR over a finished video for the same strings, the check the DOM cannot do. |

Prerequisites: Node with Playwright and Chromium (`npm install` then
`npx playwright install chromium` in `recordings/`), `Xvfb`, `ffmpeg`, and
Python with Pillow, plus `rapidocr_onnxruntime` for the OCR check. Fonts come
from Liberation Sans.

1. Copy `targets.example.json` to `targets.json` and fill it in. It is
   gitignored. `leakPatterns` are regular expressions for your hostname, your
   feed host and anything else that identifies the instance; `excludeShows`
   names podcasts that must stay out of frame.
2. Start one virtual display per device shape. The sizes are the pixel sizes
   the recorder expects:

   ```bash
   Xvfb :97 -screen 0 1170x2532x24 -nolisten tcp &   # phone
   Xvfb :96 -screen 0 2000x1800x24 -nolisten tcp &   # laptop
   Xvfb :98 -screen 0 2880x1800x24 -nolisten tcp &   # desktop
   ```

3. Log in to the web UI and pass the session cookie, either as
   `MINUSPOD_SESSION=<value>` or as `MINUSPOD_COOKIES=/path/to/cookies.txt`.
4. Record the beats of a video, phone beats and laptop beats in separate runs:

   ```bash
   cd docs/videos/recordings
   node shoot.mjs beats/review.mjs phone queue
   node shoot.mjs beats/review.mjs laptop held modal confirm after
   ```

   Clips land in `clips/<video>/<beat>.mp4`. Pull a frame with
   `ffmpeg -ss 3 -i clips/review/held.mp4 -frames:v 1 check.png` and look at it
   before moving on. A clip is cheap to redo.
5. Compose both cuts:

   ```bash
   python3 compose.py beats/review.json portrait
   python3 compose.py beats/review.json landscape
   ```

   The result is `out/<output>.mp4` and `out/<output>-landscape.mp4`, named by
   the `output` field of the beats file. Pass beat indexes after the mode to
   rebuild only those segments while tuning a crop or a caption.

A beat may export a `mock(page, T)` hook that registers `page.route` handlers before navigation. The YouTube beats use it to answer rating, submit and generate in the page, so the instance is untouched.

The UI tour beats live in `beats/demo.mjs` and are read-only: they navigate, scroll and open dialogs, and never save, confirm, rate, submit or generate anything.

Some beats change the instance: the review and patterns videos each confirm
a held marker and recut its episode, the patterns video sets one pattern's
category and starts a Pattern Cleanup run, the local feed video creates a
feed and uploads an episode, and processing an uploaded episode spends one
detection call. Take a
configuration export and a database backup first, and point `targets.json` at
an episode you are happy to see change.

Three safeguards keep instance details out of the video. Before a take, and
every 400 ms while it runs, `hideSensitive` walks the rendered text and input
values, a dropdown's selected value included. It blanks the row or field
around anything that matches a pattern from `targets.json`, and around any URL
in an input. `leakCheck` runs the same walk and refuses the take if a match is
still visible, so a dialog or toast opened mid-beat is covered too. After
composing, `ocrcheck.py out/*.mp4` reads every second of the finished video,
because the frontend can draw text the DOM check does not see, such as chart
labels on a canvas.

To add a video, write `beats/<name>.mjs` with a `BEATS` object (`dev`, `url`
or a function of the targets, optional `prep`, `run`), a matching
`beats/<name>.json` with the `output` name and the beat list, and record. A
laptop crop is given in source pixels (`[x, y, w, h]` on the 2000x1800
capture); the landscape cut reshapes it around the same center.

The feed excerpt in the Podcasting 2.0 video is not a recording. Fetch the
served RSS of the demo feed and run `node feedcard.mjs feed.xml`: every URL
loses its query string, every host except the namespace hosts becomes
`example.com`, and the result is an 8 s laptop-sized clip at
`clips/p20/feedxml.mp4` that the composer frames like any other.

## Embedding in the docs

GitHub shows a video player only for files uploaded through its own editor, as
`github.com/user-attachments/assets/...` URLs. A video committed in the repo,
linked or wrapped in a `<video>` tag, renders as a plain link. So each
landscape cut was dropped into a comment on the pull request that added it,
and the doc that covers the feature carries that URL on a line of its own:

| Video | Embedded in |
|---|---|
| UI tour | [Project README](../../README.md), [Web Interface](../web-interface.md) |
| How it works | [How It Works](../how-it-works.md) |
| Review queue | [Web Interface, Held for Review](../web-interface.md#held-for-review) |
| Audio cues | [Audio Cue Detection](../audio-cues.md) |
| Processing modes | [Episode Processing Workflows, Processing modes](../workflows.md#processing-modes) |
| Local feeds | [Local Feeds](../local-feeds.md) |
| Providers and cost | [LLM Providers](../llm-providers.md) |
| Podcasting 2.0 | [Podcasting 2.0](../podcasting-2.0.md) |
| Patterns | [Pattern Cleanup](../pattern-cleanup.md) |
| YouTube feeds | [YouTube Feeds](../youtube-feeds.md) |

To replace a video, re-record or re-render it and commit the new files here.
Then drag the new landscape file into a comment on a pull request or issue in
this repository, copy the asset URL GitHub puts in the comment, and swap it in
on the doc above. The upload cap is 10 MB per file on a free plan; every
landscape cut here is under it. Keep the comment, so there is a record of where
the asset came from.

[< Docs index](../README.md) | [Project README](../../README.md)
