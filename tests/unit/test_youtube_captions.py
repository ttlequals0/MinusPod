from youtube import captions

JSON3 = {'events': [
    {'tStartMs': 0, 'dDurationMs': 2000, 'segs': [{'utf8': 'Hello', 'tOffsetMs': 0}, {'utf8': ' there', 'tOffsetMs': 800}]},
    {'tStartMs': 2000, 'dDurationMs': 100, 'segs': [{'utf8': '\n'}]},
    {'tStartMs': 2100, 'dDurationMs': 300, 'segs': [{'utf8': 'friend', 'tOffsetMs': 0}]},
    {'tStartMs': 5000, 'dDurationMs': 1500, 'segs': [{'utf8': 'Next', 'tOffsetMs': 0}, {'utf8': ' line', 'tOffsetMs': 700}]},
    {'tStartMs': 9000, 'dDurationMs': 1000},
]}


def test_json3_to_segments_merges_short_events_and_keeps_words():
    segs = captions.json3_to_segments(JSON3)
    assert [s['text'] for s in segs] == ['Hello there friend', 'Next line']
    assert segs[0]['start'] == 0.0 and segs[0]['end'] == 2.4
    assert segs[0]['words'][0] == {'word': 'Hello', 'start': 0.0, 'end': 0.8}
    assert segs[0]['words'][-1] == {'word': 'friend', 'start': 2.1, 'end': 2.4}
    assert segs[1]['words'][1]['start'] == 5.7


def test_pick_file_prefers_creator_track(tmp_path):
    for name in ('v.en-orig.json3', 'v.en.json3', 'v.de.json3'):
        (tmp_path / name).write_text('{}')
    assert captions.pick_file(str(tmp_path), 'v', 'en').endswith('v.en.json3')
    (tmp_path / 'v.en.json3').unlink()
    assert captions.pick_file(str(tmp_path), 'v', 'en').endswith('v.en-orig.json3')
    assert captions.pick_file(str(tmp_path), 'v', 'fr').endswith('.json3')
    assert captions.pick_file(str(tmp_path), 'zz', 'en') is None


def test_pick_file_auto_or_empty_accepts_any_track(tmp_path):
    (tmp_path / 'v.de-orig.json3').write_text('{}')
    for lang in ('auto', '', None):
        assert captions.pick_file(str(tmp_path), 'v', lang).endswith('v.de-orig.json3')


def _event(start, dur, *tokens):
    return {'tStartMs': start, 'dDurationMs': dur,
            'segs': [{'utf8': t, 'tOffsetMs': off} for off, t in tokens]}


def test_speaker_marker_is_stripped_and_word_timings_kept():
    segs = captions.json3_to_segments({'events': [_event(0, 2000, (0, '>> Back'), (800, ' room.'))]})
    assert [s['text'] for s in segs] == ['Back room.']
    assert segs[0]['words'] == [{'word': 'Back', 'start': 0.0, 'end': 0.8},
                                {'word': 'room.', 'start': 0.8, 'end': 2.0}]


def test_cue_only_event_produces_no_segment():
    data = {'events': [_event(0, 2000, (0, '[Music]')), _event(3000, 1000, (0, '(Upbeat Music)')),
                       _event(5000, 1000, (0, '>>>'))]}
    assert captions.json3_to_segments(data) == []


def test_mixed_cue_and_speech_keeps_speech():
    segs = captions.json3_to_segments({'events': [_event(0, 2000, (0, '[laughter]'), (500, ' okay'))]})
    assert [s['text'] for s in segs] == ['okay']
    assert segs[0]['words'][0]['start'] == 0.5


def test_spoken_parentheticals_stay_but_sound_cues_go():
    keep = captions.json3_to_segments({'events': [_event(0, 2000, (0, '(you know) sure'))]})
    assert [s['text'] for s in keep] == ['(you know) sure']
    cue = captions.json3_to_segments({'events': [_event(0, 2000, (0, '(upbeat music) sure'))]})
    assert [s['text'] for s in cue] == ['sure']
