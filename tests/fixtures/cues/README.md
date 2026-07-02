Real-audio cue fixtures for integration tests (issue #350)

These FLAC files are short content-transition stingers contributed publicly by
users on GitHub issue #350 for the purpose of tuning MinusPod's audio cue
detection. They are used by tests/integration/test_cue_matcher_real_audio.py.

Files
-----

wsj_content_transition.flac
  Source: WSJ The Journal podcast content-transition jingle.
  Format: 16 kHz mono s16 FLAC, ~2.82 s.
  Origin: Uploaded as a ZIP attachment by user "apparle" on 2026-06-29 in
          GitHub issue https://github.com/ttlequals0/MinusPod/issues/350.
  RSS: https://video-api.wsj.com/podcast/rss/wsj/the-journal

pivot_content_transition.flac
  Source: Pivot podcast content-transition sting.
  Format: 16 kHz mono s16 FLAC, ~2.30 s.
  Origin: Uploaded as a ZIP attachment by user "apparle" on 2026-07-01 in
          GitHub issue https://github.com/ttlequals0/MinusPod/issues/350.
  RSS: https://feeds.megaphone.fm/pivot

Provenance
----------

Both files are excerpts under 3 seconds. They were posted publicly on the
linked issue for the explicit purpose of tuning MinusPod's cue detector.
No redistribution rights beyond the repo test fixtures are claimed.
