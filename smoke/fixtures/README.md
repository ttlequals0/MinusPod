# Smoke test fixtures

Static files used by smoke tests that need real-feed-shaped input. Kept
small (<1 MB total) so the repo doesn't bloat. Larger artifacts (full
podcast episodes) are downloaded on demand by the tests that need them.

## Files

- `tiny-feed.xml`: minimal valid RSS 2.0 with one episode pointing at
  `tiny-episode.mp3`. Used by T08-artwork (artwork validator), T12-rss-
  public-paths (public feed routes), and the planned pattern correction
  tests.
- `tiny-episode.mp3`: ~5s of silence at 22.05 kHz / 64 kbps. Real MP3
  bytes so transcription/audio-analysis paths don't error on a fake
  payload, but short enough that the full pipeline runs in seconds.
- `bad-artwork-html.jpg`: HTML body with Content-Type=image/jpeg lie;
  used by T08 to assert the artwork validator rejects.

## Generation

`tiny-episode.mp3` is generated from silence; regenerate with:

```
ffmpeg -f lavfi -i anullsrc=channel_layout=mono:sample_rate=22050 \
       -t 5 -b:a 64k smoke/fixtures/tiny-episode.mp3
```

`tiny-feed.xml` is hand-edited; keep `<guid>` deterministic so a re-
import doesn't double-add the episode.
