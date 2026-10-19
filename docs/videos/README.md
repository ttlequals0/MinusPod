# Videos

[< Docs index](../README.md) | [Project README](../../README.md)

Short vertical videos about MinusPod, cut for TikTok and other 9:16 feeds.
Both are 1080x1920 at 30 fps, H.264, with no audio track, so a caption or
music can be added in the posting app.

| File | Length | What it shows |
|---|---|---|
| [minuspod-demo-tiktok.mp4](minuspod-demo-tiktok.mp4) | 85 s | A tour of the web UI: dashboard, processing queue, detected ads, the waveform editor, review, patterns, search, stats, settings and themes. Recorded from a running instance. |
| [minuspod-how-it-works-tiktok.mp4](minuspod-how-it-works-tiktok.mp4) | 76 s | An animated walk through one episode: how work arrives, transcription, the five kinds of evidence, the confidence gate, the cut, the verification pass, publishing, the learning loop and what happens when something fails. Follows [How It Works](../how-it-works.md) and [Episode Processing Workflows](../workflows.md). |

## Rebuilding the how-it-works video

The animation is a single HTML canvas in [how-it-works/index.html](how-it-works/index.html).
Every frame is a pure function of time, so `render.mjs` steps `window.render(t)`
at 30 fps, screenshots each frame with Playwright, and ffmpeg encodes the sequence.
The logo comes from `frontend/public/logo-dark.svg` and the colors mirror the
app's dark theme tokens, so the video tracks the app's own look.

```bash
cd docs/videos/how-it-works
npx -y -p playwright node render.mjs frames
ffmpeg -framerate 30 -i frames/%05d.png -c:v libx264 -crf 18 -preset slow \
  -pix_fmt yuv420p -movflags +faststart ../minuspod-how-it-works-tiktok.mp4
rm -rf frames
```

`node render.mjs preview 2,25.5,49` writes single frames at those times to
`preview/` for checking a scene without a full render. Rendering needs network
access for the Roboto web font; Playwright's Chromium must be installed
(`npx playwright install chromium`).

Scenes, captions and timings live in the `scenes` list at the bottom of
`index.html`. Each scene is a duration, a headline, a subline, a draw function
of local time, and the pipeline stage to light up in the footer rail.

The demo video is a screen recording; there is nothing to rebuild.

[< Docs index](../README.md) | [Project README](../../README.md)
