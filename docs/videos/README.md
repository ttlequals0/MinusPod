# Videos

[< Docs index](../README.md) | [Project README](../../README.md)

Short videos about MinusPod, each in a portrait cut for TikTok and other 9:16
feeds and a landscape cut for YouTube, a README or a talk. Portrait is 1080x1920
and landscape is 1920x1080, both 30 fps H.264 with no audio track, so a caption
or music can be added in the posting app.

| Video | Portrait | Landscape | Length | What it shows |
|---|---|---|---|---|
| UI tour | [minuspod-demo.mp4](minuspod-demo.mp4) | [minuspod-demo-landscape.mp4](minuspod-demo-landscape.mp4) | 85 s | Dashboard, processing queue, detected ads, the waveform editor, review, patterns, search, stats, settings and themes. Recorded from a running instance. |
| How it works | [minuspod-how-it-works.mp4](minuspod-how-it-works.mp4) | [minuspod-how-it-works-landscape.mp4](minuspod-how-it-works-landscape.mp4) | 107 s | One episode across all eleven pipeline stages: download, transcribe, second download and audio analysis, detect (free matches, then the model), validate, boundary review, cut, verify pass, transcript and chapters, publish. Then the learning loop and what happens when something fails. Follows [How It Works](../how-it-works.md) and [Episode Processing Workflows](../workflows.md). |

## Rebuilding the how-it-works video

The animation is a single HTML canvas in [how-it-works/index.html](how-it-works/index.html).
Every frame is a pure function of time, so `render.mjs` steps `window.render(t)`
at 30 fps, screenshots each frame with Playwright, and ffmpeg encodes the sequence.
The logo comes from `frontend/public/logo-dark.svg` and the colors mirror the
app's dark theme tokens, so the video tracks the app's own look.

```bash
cd docs/videos/how-it-works
npm install                      # playwright, once
npx playwright install chromium  # once, if Chromium is not installed yet
npm run frames && npm run encode                      # portrait
npm run frames:landscape && npm run encode:landscape  # landscape
rm -rf frames
```

The scenes are drawn once, in portrait coordinates. The landscape layout keeps
the caption and the stage rail in a left column and scales the scene content
into the right side of the frame. `node render.mjs preview --landscape` previews
that layout.

`node render.mjs preview 2,25.5,49` writes single frames at those times to
`preview/` for checking a scene without a full render; with no times it
writes the midpoint of every scene. Rendering needs network
access for the Roboto web font. `frames/` and `preview/` are gitignored.

Scenes, captions and timings live in the `scenes` list at the bottom of
`index.html`. Each scene is a duration, a headline, a subline, a draw function
of local time, and the pipeline stages to light up in the footer rail. The
episode runtime and ad positions are constants at the top, and every duration
label in the video is derived from them.

The demo video is a screen recording; there is nothing to rebuild.

[< Docs index](../README.md) | [Project README](../../README.md)
