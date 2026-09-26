"""Pattern learning uses only words inside the accepted marker."""

from text_pattern_matcher import TextPatternMatcher, _segments_for_pattern_learning


AD = (
    "Acme Tools keeps your workshop running without the usual hassle. "
    "Every Acme Tools order ships quickly and arrives ready to use. "
    "Visit Acme Tools online for the current listener offer today."
)
SECOND_AD = (
    "Beta Corp files your small business taxes in a single afternoon. "
    "Beta Corp reads every form so you never have to open one. "
    "Try Beta Corp online and see the difference for yourself."
)


def _timed_segment(*parts):
    text = ' '.join(part[0] for part in parts)
    words = []
    for passage, start, end in parts:
        tokens = passage.split()
        step = (end - start) / len(tokens)
        words.extend(
            {'word': token, 'start': start + index * step,
             'end': start + (index + 1) * step}
            for index, token in enumerate(tokens)
        )
    return {'start': parts[0][1], 'end': parts[-1][2],
            'text': text, 'words': words}


def test_saved_template_excludes_host_words_outside_cut(temp_db):
    temp_db.create_known_sponsor('Acme Tools')
    segment = _timed_segment(
        ('Welcome back to the program.', 0.0, 10.0),
        (AD, 10.0, 30.0),
        ('Now the hosts return to their discussion.', 30.0, 40.0),
    )
    matcher = TextPatternMatcher(db=temp_db)

    created = matcher.create_patterns_from_ad(
        [segment], 10.0, 30.0, sponsor='Acme Tools',
        podcast_id='example-podcast')

    assert len(created) == 1
    template = temp_db.get_ad_pattern_by_id(created[0]['id'])['text_template']
    assert template.startswith('Acme Tools')
    assert template.endswith('today.')
    assert 'Welcome back' not in template
    assert 'hosts return' not in template


def test_direct_creation_clips_partial_segment(temp_db):
    temp_db.create_known_sponsor('Acme Tools')
    segment = _timed_segment(
        ('Welcome back to the program.', 0.0, 10.0),
        (AD, 10.0, 30.0),
        ('Now the hosts return to their discussion.', 30.0, 40.0),
    )
    matcher = TextPatternMatcher(db=temp_db)

    pattern_id = matcher.create_pattern_from_ad(
        [segment], 10.0, 30.0, sponsor='Acme Tools',
        podcast_id='example-podcast')

    assert pattern_id is not None
    template = temp_db.get_ad_pattern_by_id(pattern_id)['text_template']
    assert 'Welcome back' not in template
    assert 'hosts return' not in template


def test_split_inside_one_timed_segment_keeps_ad_text_separate(temp_db):
    temp_db.create_known_sponsor('Acme Tools')
    temp_db.create_known_sponsor('Beta Corp')
    segment = _timed_segment((AD, 0.0, 20.0), (SECOND_AD, 20.0, 40.0))
    matcher = TextPatternMatcher(db=temp_db)

    created = matcher.create_patterns_from_ad(
        [segment], 0.0, 40.0, sponsor='Acme Tools',
        podcast_id='example-podcast',
        ad={'merged_member_spans': [
            {'start': 0.0, 'end': 20.0, 'sponsor': 'Acme Tools'},
            {'start': 20.0, 'end': 40.0, 'sponsor': 'Beta Corp'},
        ]})

    assert len(created) == 2
    first = temp_db.get_ad_pattern_by_id(created[0]['id'])['text_template']
    second = temp_db.get_ad_pattern_by_id(created[1]['id'])['text_template']
    assert 'Beta Corp' not in first
    assert 'Acme Tools' not in second


def test_partial_segment_with_missing_timed_word_is_not_learned(temp_db):
    temp_db.create_known_sponsor('Acme Tools')
    segment = _timed_segment(
        ('Welcome back to the program.', 0.0, 10.0),
        (AD, 10.0, 30.0),
        ('Now the hosts return to their discussion.', 30.0, 40.0),
    )
    segment['words'] = [word for word in segment['words']
                        if word['word'] != 'hosts']

    matcher = TextPatternMatcher(db=temp_db)
    assert matcher.create_patterns_from_ad(
        [segment], 10.0, 30.0, sponsor='Acme Tools',
        podcast_id='example-podcast') == []
    assert temp_db.get_ad_patterns(podcast_id='example-podcast') == []


def test_direct_creation_declines_untimed_partial_segment(temp_db):
    temp_db.create_known_sponsor('Acme Tools')
    segment = {'start': 0.0, 'end': 40.0,
               'text': f'Welcome back. {AD} Now the hosts return.'}
    matcher = TextPatternMatcher(db=temp_db)

    assert matcher.create_pattern_from_ad(
        [segment], 10.0, 30.0, sponsor='Acme Tools',
        podcast_id='example-podcast') is None
    assert matcher.create_pattern_from_ad(
        [{'start': 10.0, 'end': 30.0, 'text': AD}],
        10.0, 30.0, sponsor='Acme Tools',
        podcast_id='example-podcast') is not None


def test_divider_inside_untimed_segment_declines_learning(temp_db):
    temp_db.create_known_sponsor('Acme Tools')
    temp_db.create_known_sponsor('Beta Corp')
    matcher = TextPatternMatcher(db=temp_db)
    segment = {'start': 0.0, 'end': 40.0,
               'text': f'{AD} {SECOND_AD}'}

    assert matcher.create_patterns_from_ad(
        [segment], 0.0, 40.0, sponsor='Acme Tools',
        podcast_id='example-podcast',
        ad={'merged_member_spans': [
            {'start': 0.0, 'end': 20.0, 'sponsor': 'Acme Tools'},
            {'start': 20.0, 'end': 40.0, 'sponsor': 'Beta Corp'},
        ]}) == []
    assert temp_db.get_ad_patterns(podcast_id='example-podcast') == []


def test_decimal_end_keeps_last_ad_word_but_not_first_host_word():
    segment = _timed_segment(
        ('Welcome back.', 0.0, 10.0),
        (AD, 10.0, 30.0),
        ('Now the hosts return.', 30.0, 40.0),
    )
    clipped = _segments_for_pattern_learning([segment], 10.0, 29.99)
    text = ' '.join(word['text'] for word in clipped)
    assert 'today.' in text
    assert 'Now' not in text
