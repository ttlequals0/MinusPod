"""text_recurrence: spans of the current transcript whose wording repeats
near-verbatim across prior episodes."""
from text_recurrence import find_recurring_spans, format_recurrence_hint

INTRO = ("welcome to the weekly show where we discuss science and "
         "science based tools for everyday life with your host")


def _segs(*texts, dur=10.0):
    out, t = [], 0.0
    for text in texts:
        out.append({'start': t, 'end': t + dur, 'text': text})
        t += dur
    return out


def test_recurring_intro_found_across_two_priors():
    current = _segs(INTRO, "today we talk about sleep", "unique content here")
    priors = [_segs(INTRO, "today we talk about diet"),
              _segs(INTRO, "today we talk about focus")]
    spans = find_recurring_spans(current, priors)
    assert len(spans) == 1
    assert spans[0]['start'] == 0.0
    assert spans[0]['end'] == 10.0
    assert "welcome to the weekly show" in spans[0]['text']


def test_requires_two_priors():
    current = _segs(INTRO)
    assert find_recurring_spans(current, [_segs(INTRO)]) == []
    assert find_recurring_spans(current, []) == []


def test_one_off_short_phrases_not_flagged():
    current = _segs("thanks for listening everyone see you next time maybe")
    priors = [_segs("totally different episode about gardening and soil"),
              _segs("another episode about baking bread at home today")]
    assert find_recurring_spans(current, priors) == []


def test_in_episode_repetition_alone_does_not_trigger():
    # The shingle appears twice in ONE prior but not in a second prior.
    current = _segs(INTRO)
    priors = [_segs(INTRO, INTRO),
              _segs("entirely unrelated words that never repeat anywhere else")]
    assert find_recurring_spans(current, priors) == []


def test_gap_bridging_merges_across_one_segment():
    current = _segs(INTRO, "a short unique aside right here", INTRO)
    priors = [_segs(INTRO, INTRO), _segs(INTRO, INTRO)]
    spans = find_recurring_spans(current, priors)
    assert len(spans) == 1
    assert spans[0]['start'] == 0.0
    assert spans[0]['end'] == 30.0


def test_min_span_seconds_filter():
    current = _segs(INTRO, dur=3.0)  # 3s span, below the 5s floor
    priors = [_segs(INTRO), _segs(INTRO)]
    assert find_recurring_spans(current, priors) == []


def test_hint_filters_to_window_and_empty_cases():
    spans = [{'start': 0.0, 'end': 10.0, 'text': 'intro spiel'},
             {'start': 500.0, 'end': 520.0, 'text': 'credits roll'}]
    hint = format_recurrence_hint(spans, 0.0, 60.0)
    assert 'intro spiel' in hint and 'credits roll' not in hint
    assert format_recurrence_hint(spans, 100.0, 400.0) == ""
    assert format_recurrence_hint([], 0.0, 60.0) == ""
