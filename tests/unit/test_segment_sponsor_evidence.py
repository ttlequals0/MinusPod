"""Guessed segment sponsors need brand evidence before they become sponsor rows."""
from api.patterns import _create_patterns_from_segments
from text_pattern_matcher import TextPatternMatcher

SLUG = 'example-podcast'
DAY_TEXT = (
    "Day is the easiest way to shop. Enjoy 365 day returns on everything. "
    "That is 365 day returns, no questions asked, from Day."
)
ACME_TEXT = (
    "Acme makes sturdy gear. Head to acme.com for ten percent off. "
    "Acme ships fast, so try Acme now."
)


def _create(db, segments, primary, **kwargs):
    return _create_patterns_from_segments(
        db, segments, scope='podcast', podcast_id_str=SLUG, network_id=None,
        episode_id='a1b2c3d4e5f6', primary_sponsor=primary, log_context='test', **kwargs)


def _sponsor_id(db, pid):
    return db.get_ad_pattern_by_id(pid)['sponsor_id']


def test_unbacked_guessed_sponsor_gets_no_row(temp_db):
    segs = [{'text': ACME_TEXT, 'sponsor': 'Acme'}, {'text': DAY_TEXT, 'sponsor': 'Day'}]
    _, ids = _create(temp_db, segs, 'Acme')
    assert len(ids) == 2
    assert _sponsor_id(temp_db, ids[1]) is None
    assert temp_db.get_known_sponsor_by_name('Day') is None


def test_backed_guessed_sponsor_keeps_row(temp_db):
    segs = [{'text': DAY_TEXT, 'sponsor': 'Day'}, {'text': ACME_TEXT, 'sponsor': 'Acme'}]
    _, ids = _create(temp_db, segs, 'Day')
    assert _sponsor_id(temp_db, ids[1]) is not None
    assert temp_db.get_known_sponsor_by_name('Acme') is not None


def test_untrusted_unbacked_primary_gets_no_row(temp_db):
    segs = [{'text': DAY_TEXT, 'sponsor': 'Day'}]
    pid, _ = _create(temp_db, segs, 'Day', primary_sponsor_trusted=False)
    assert pid is not None
    assert _sponsor_id(temp_db, pid) is None
    assert temp_db.get_known_sponsor_by_name('Day') is None


def test_trusted_primary_keeps_row(temp_db):
    pid, _ = _create(temp_db, [{'text': DAY_TEXT, 'sponsor': 'Day'}], 'Day')
    assert _sponsor_id(temp_db, pid) is not None
    assert temp_db.get_known_sponsor_by_name('Day') is not None


def test_split_pattern_unbacked_guess_leaves_no_sponsor(temp_db):
    text = (
        "This episode is brought to you by Day. Day is the easiest way to shop, "
        "with 365 day returns on everything. "
        "This episode is sponsored by Acme. Acme makes sturdy gear, so head to "
        "acme.com for ten percent off today."
    )
    matcher = TextPatternMatcher(db=temp_db)
    pid = temp_db.create_ad_pattern(
        scope='podcast', text_template=text, podcast_id=SLUG,
        created_from_episode_id='a1b2c3d4e5f6', source_language='en')
    ids = matcher.split_pattern(pid)
    assert len(ids) == 2
    assert _sponsor_id(temp_db, ids[0]) is None
    assert _sponsor_id(temp_db, ids[1]) is not None
    assert temp_db.get_known_sponsor_by_name('Day') is None
