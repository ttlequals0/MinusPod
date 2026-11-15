"""Show-bound segment core: normalisation, keys, stable cores and matching (fixtures use a fake show)."""
import pytest

import community_segments_core as cs

GUID = '3f1c9a2e-0b7d-5e44-9c1a-7d2e8f6a1b30'
OTHER_GUID = '9a8b7c6d-0000-5e44-9c1a-000000000001'

ASK = ("If you're enjoying The Copper Kettle Hour, leave us a review in your podcast app, follow us at "
       "@copperkettlehour, and send your questions to <email> or call <phone>. "
       "This week's question came from Priya in Leeds.")


# ---------------------------------------------------------------- normalisation (T-NORM-1..4)

def test_normalize_lowercases_and_strips_punctuation():
    assert cs.normalize_for_segments("Hello, WORLD!  It's   here.") == 'hello world it s here'


def test_handles_urls_domains_and_numbers_become_placeholders():
    out = cs.normalize_for_segments(
        'follow @copperkettlehour, visit https://kettle.example/join or kettlehour.example and episode 41')
    assert out == 'follow [handle] visit [url] or [url] and episode [num]'


def test_worked_example_normalises_without_pii():
    out = cs.normalize_for_segments(ASK)
    assert '[handle]' in out and 'copperkettlehour' not in out
    assert out.startswith('if you re enjoying the copper kettle hour leave us a review')


def test_shingles_are_word_trigrams():
    assert cs.shingles(['a', 'b', 'c', 'd']) == ['a b c', 'b c d']
    assert cs.shingles(['a', 'b']) == []


# ---------------------------------------------------------------- keys (T-KDF-1, T-KDF-2)

KNOWN_VECTOR = (646, 'ac081f')  # the fake show's guid


def test_published_entries_keep_the_v1_bucket_and_fingerprint():
    # Installs match each other's published entries, so the bucket, KDF and truncation must not drift.
    bucket = cs.bucket_for(GUID)
    assert 0 <= bucket < 1024
    assert (bucket, cs.fingerprint(cs.show_secret(GUID), 'leave us a')) == KNOWN_VECTOR


def test_secret_is_per_show_and_fingerprint_is_24_bits():
    secret = cs.show_secret(GUID)
    assert cs.show_secret(GUID) is secret  # cached per key
    fp = cs.fingerprint(secret, 'leave us a')
    assert len(fp) == 6 and int(fp, 16) < 2 ** 24
    assert fp == cs.fingerprint(secret, 'leave us a')
    assert fp != cs.fingerprint(cs.show_secret(OTHER_GUID), 'leave us a')


def test_scrypt_failure_is_loud(monkeypatch):
    def boom(*a, **k):
        raise ValueError('memory limit exceeded')
    monkeypatch.setattr(cs.hashlib, 'scrypt', boom)
    with pytest.raises(cs.SegmentKeyError):
        cs.show_secret('a-key-not-cached-elsewhere')


# ---------------------------------------------------------------- stable core (T-CORE-1)

def _episode_ask(guest):
    return ("If you're enjoying the show, leave us a review in your podcast app and follow us so you never "
            f"miss an episode. Send your questions in, this week's question came from {guest}.")


def test_stable_core_drops_one_off_words_and_keeps_the_recurring_ask():
    texts = [_episode_ask(g) for g in ('Priya in Leeds', 'Tomas in Graz', 'Ines in Porto',
                                       'Kofi in Accra', 'Lena in Bergen')]
    core = cs.stable_core(texts, short_ask=False)
    assert 'leave us a' in core and 'came from priya' not in core and 'priya in leeds' not in core
    assert core.index('if you re') < core.index('leave us a')  # spoken order


def test_stable_core_needs_two_episodes():
    assert cs.stable_core([_episode_ask('Priya in Leeds')], short_ask=False) == []


def test_short_ask_core_needs_eighty_percent():
    texts = ['please rate and review the show'] * 4 + ['please rate the show today']
    core = cs.stable_core(texts, short_ask=True)
    assert 'rate and review' in core  # 4/5 = 80 %
    assert 'rate the show' not in core  # 1/5


# ---------------------------------------------------------------- matching (T-MATCH-1..3, T-ASK-3)

FILLER = ("we talked about the history of tea houses and the way kettles changed over the centuries "
          "from iron to copper and later to electric models that boil water in a minute ").split()


def _segments(words, start=0.0, step=0.4, per_segment=12):
    segs, t = [], start
    for i in range(0, len(words), per_segment):
        chunk = words[i:i + per_segment]
        segs.append({'start': round(t, 2), 'end': round(t + step * len(chunk), 2), 'text': ' '.join(chunk) + '.'})
        t += step * len(chunk)
    return segs


def _entry_for(texts, guid=GUID, category='interaction', short_ask=False, durations=(8.0, 10.0, 12.0)):
    core = cs.stable_core(texts, short_ask=short_ask)
    return cs.entry_body(core, guid, 'podcast_guid', category=category, short_ask=short_ask,
                         durations=list(durations), language='en', confirms=5, episodes=5, span_days=21)


def _noisy(words, rate):
    """Replace every 1/rate-th word, offset by half a period: a deterministic word error rate."""
    period = round(1 / rate)
    return [('uh' if i % period == period // 2 else w) for i, w in enumerate(words)]


def test_long_entry_matches_with_ten_percent_word_errors():
    ask = _episode_ask('Priya in Leeds')
    entry = _entry_for([_episode_ask(g) for g in ('A one', 'B two', 'C three', 'D four', 'E five')])
    words = FILLER * 3 + _noisy(cs.normalize_for_segments(ask).split(), 0.10) + FILLER * 2
    matches = cs.find_matches(entry, cs.show_secret(GUID), _segments(words), episode_duration=600.0)
    assert len(matches) == 1 and matches[0].c_body >= 0.55


def test_reworded_middle_with_anchors_intact_still_matches():
    entry = _entry_for([_episode_ask(g) for g in ('A one', 'B two', 'C three', 'D four', 'E five')])
    words = cs.normalize_for_segments(_episode_ask('Zed')).split()
    words[8:14] = ['and', 'tell', 'a', 'friend', 'about', 'us']
    matches = cs.find_matches(entry, cs.show_secret(GUID), _segments(FILLER + words + FILLER), 600.0)
    assert len(matches) == 1


def test_generic_review_ask_on_another_show_never_matches():
    entry = _entry_for([_episode_ask(g) for g in ('A one', 'B two', 'C three', 'D four', 'E five')])
    other = cs.normalize_for_segments(_episode_ask('Priya')).split()
    # Same words, but the receiver derives the other show's secret: random hashes.
    assert cs.find_matches(entry, cs.show_secret(OTHER_GUID), _segments(FILLER + other + FILLER), 600.0) == []
    generic = 'leave us a review and follow the show thanks'.split()
    assert cs.find_matches(entry, cs.show_secret(GUID), _segments(FILLER + generic + FILLER), 600.0) == []


def test_match_edges_come_from_the_receivers_transcript():
    entry = _entry_for([_episode_ask(g) for g in ('A one', 'B two', 'C three', 'D four', 'E five')])
    ask_words = cs.normalize_for_segments(_episode_ask('Zed')).split()
    segs = _segments(FILLER + ask_words + FILLER, per_segment=len(FILLER))
    m = cs.find_matches(entry, cs.show_secret(GUID), segs, 600.0)[0]
    ask_seg = segs[1]
    assert abs(m.start - ask_seg['start']) < 0.5
    assert m.end <= ask_seg['end'] + 0.01 and m.end > ask_seg['end'] - 4.0


SHORT = 'please rate and review the copper kettle hour on apple podcasts'


def test_short_ask_entry_matches_near_exactly_only():
    entry = _entry_for([SHORT] * 5, short_ask=True, durations=(3.0, 4.0, 5.0))
    assert entry['short_ask'] is True and len(entry['head']) == 3 and len(entry['tail']) == 3
    secret = cs.show_secret(GUID)
    exact = _segments(FILLER + SHORT.split() + FILLER)
    assert len(cs.find_matches(entry, secret, exact, 600.0)) == 1
    partial = 'please rate and review the show on a different app'.split()
    assert cs.find_matches(entry, secret, _segments(FILLER + partial + FILLER), 600.0) == []


def test_position_prior_for_intro_requires_the_start():
    intro = 'welcome to the copper kettle hour the show about tea kettles and the people who love them'
    entry = _entry_for([intro] * 5, category='intro', durations=(5.0, 6.0, 7.0))
    assert entry['position']['anchor'] == 'start'
    secret = cs.show_secret(GUID)
    at_start = _segments(intro.split() + FILLER * 8)
    late = _segments(FILLER * 12 + intro.split() + FILLER)
    assert len(cs.find_matches(entry, secret, at_start, 900.0)) == 1
    assert cs.find_matches(entry, secret, late, 900.0) == []

