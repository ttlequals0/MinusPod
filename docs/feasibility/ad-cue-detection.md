# Feasibility: detecting audio cues that precede ad breaks

Tracking issue: #350

## The problem

Some shows play a short non-spoken sound right before an ad break: a chime or a
brief musical sting. A listener hears it and knows an ad is coming. The
transcript does not capture it, so the ad detector never sees that signal. The
reporter on #350 says ads are caught "a bit too late," which fits: the model
finds the ad once the spoken sponsor copy starts, a second or two after the cue
that actually marks the boundary.

This is a timing problem, not a detection problem. The ads are found; their
start edge lands late. An audio cue, if we can locate it, gives the boundary
the transcript cannot.

## Why the pipeline can already take this signal

MinusPod runs ffmpeg in several places today and already feeds non-transcript
audio signals to the language model:

- `src/audio_analysis/volume_analyzer.py` runs `ffmpeg ... -af ebur128` for
  per-frame loudness.
- `src/audio_analysis/transition_detector.py` pairs loud-to-quiet and
  quiet-to-loud transitions into candidate DAI splice points.
- `src/audio_peaks.py` pulls raw PCM windows with `atrim`.
- `src/audio_processor.py` does the final cut with an ffmpeg `filter_complex`.

All of them shell out through `src/utils/subprocess_registry.py` (`tracked_run`),
which handles process tracking and shutdown.

The signals flow to the model along a fixed path:

```
_run_audio_analysis (src/main_app/processing.py)
  -> AudioAnalyzer.analyze (src/audio_analysis/audio_analyzer.py:138)
       returns List[AudioSegmentSignal]  (src/audio_analysis/base.py:20)
  -> AudioEnforcer.format_for_window (src/audio_enforcer.py:27)
       renders each signal as a prompt line
  -> the first-pass ad-detection prompt
```

A new cue would be one more `AudioSegmentSignal` with a new `signal_type`. It
rides the same path. The ad-response parser, boundary refinement, and the
audio cut downstream do not change, because they consume ad spans, not signals.

## How a cue detector would attach

Three changes, no new data path:

1. New module `src/audio_analysis/ding_detector.py` that scans the audio and
   returns `AudioSegmentSignal` entries with `signal_type='ad_cue'`.
2. Instantiate it in `AudioAnalyzer.__init__` and call it inside
   `AudioAnalyzer.analyze`, right after transition detection
   (`src/audio_analysis/audio_analyzer.py:207-208`, where the existing
   `signals.extend(...)` calls are). On any error, log and continue, the same
   way transition detection already degrades.
3. Add an `elif signal.signal_type == 'ad_cue':` branch in
   `AudioEnforcer.format_for_window` (`src/audio_enforcer.py:53-63`) that prints
   the cue time and tells the model the cue marks the likely start edge of the
   next ad.

Detection thresholds belong in `config.py` next to the existing
`TRANSITION_THRESHOLD_DB` and friends.

## Detection approaches, cheapest first

### A. ffmpeg silencedetect around known boundaries

A stinger is usually bracketed by short gaps. `ffmpeg -af silencedetect`
reports those gaps. Run it over the whole file, or only near the transition
pairs the detector already produces, and treat a brief non-speech burst between
two gaps as a candidate cue.

- Pro: no new dependency, pure ffmpeg, reuses `tracked_run`.
- Con: a stinger is not the only thing that sits between two silences. False
  positives are likely without a second filter on the burst itself.

### B. Band-pass energy detection

Notification-style chimes carry most of their energy in a narrow high band
(roughly 2 to 8 kHz). Filter to that band, look for short energy spikes that
stand out from the speech baseline.

- Pro: separates a chime from speech better than silence alone.
- Con: needs a spectral pass and some tuning per show; music beds in that band
  cause noise.

### C. Template cross-correlation (recommended)

The strongest property of these cues is that the same sound repeats before
every break in a given show. Learn a short template from one confirmed cue,
then cross-correlate it across the episode to find every other occurrence.

- Pro: directly matches the "same ding every time" pattern; high precision once
  the template is known; naturally per-show.
- Con: needs a way to seed the template (a confirmed first instance, or a
  bootstrap from a transition pair), and a correlation pass over the audio.

A practical build: start with C for shows where a repeating cue is found, and
fall back to A as a coarse signal when no template is available. B is a tuning
aid for the burst test inside A.

## The caveat that keeps this safe

`AudioEnforcer` already states that audio signals are supporting evidence only.
The model must still find promotional content in the transcript before it marks
an ad; silence, a volume jump, or a chime on its own is not an ad. The cue
detector keeps that contract. It refines the start edge of an ad the model
already believes in. It does not create ads. That is exactly what the #350
report asks for, since the ads are found and only the timing is late.

## Validating it

The honest blocker is data. This needs a handful of episodes from shows that
use a clear, repeating cue, with the real break boundaries marked by hand.

- Metric: reduction in the gap between the detected ad start and the true
  boundary. Target under half a second.
- Comparison: run transition-only detection against transition-plus-cue on the
  same episodes and compare start-edge error.
- Guardrail: confirm the cue detector adds no false ads. It should only move
  edges of ads the model already flagged.

## Effort and risk

Roughly 300 to 400 lines for the detector plus tuning, in one new module and
two small edits to existing files. Integration risk is low because the output
reuses `AudioSegmentSignal` and changes nothing downstream of the prompt. The
real cost is tuning and the labelled audio to tune against, not the wiring.

## Recommendation

The wiring is cheap and the pipeline is ready. The open question is detection
quality, which cannot be answered without sample episodes. Suggested next step:
collect two or three episodes with a known repeating cue, prototype approach C
behind a config flag that defaults off, and measure the start-edge error before
committing to a default-on rollout.
