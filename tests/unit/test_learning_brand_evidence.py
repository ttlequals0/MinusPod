"""Automatic learning mints a new single-word sponsor row only when the text backs it as a brand."""
from text_pattern_matcher import TextPatternMatcher

DAY_AD = (
    "Day is the easiest way to shop. Enjoy 365 day returns on everything. "
    "That is 365 day returns, no questions asked, from Day."
)
WISE_AD = (
    "Wise makes sending money simple. Head to wise.com to start. "
    "Wise has no hidden fees, so try Wise today."
)
WISE_NO_EVIDENCE_AD = (
    "Wise makes sending money simple. Head over and start a transfer. "
    "Wise has no hidden fees, so try Wise today."
)


def _learn(db, sponsor, text):
    return TextPatternMatcher(db=db).create_pattern_from_ad(
        segments=[{'start': 0.0, 'end': 60.0, 'text': text}],
        start=0.0, end=60.0, sponsor=sponsor, scope='podcast',
        podcast_id='example-podcast', episode_id='a1b2c3d4e5f6')


def test_common_word_without_evidence_creates_no_sponsor_row(temp_db):
    assert _learn(temp_db, 'Day', DAY_AD) is None
    assert temp_db.get_known_sponsor_by_name('Day') is None


def test_single_word_brand_with_a_domain_creates_the_row(temp_db):
    assert _learn(temp_db, 'Wise', WISE_AD) is not None
    assert temp_db.get_known_sponsor_by_name('Wise') is not None


def test_existing_row_is_reused_without_evidence(temp_db):
    sponsor_id = temp_db.create_known_sponsor(name='Wise')
    pattern_id = _learn(temp_db, 'Wise', WISE_NO_EVIDENCE_AD)
    assert pattern_id is not None
    assert temp_db.get_ad_pattern_by_id(pattern_id)['sponsor_id'] == sponsor_id


def test_existing_alias_counts_as_a_row(temp_db):
    temp_db.create_known_sponsor(name='Wise Transfers', aliases=['Wise'])
    assert _learn(temp_db, 'Wise', WISE_NO_EVIDENCE_AD) is not None


def test_inactive_row_does_not_exempt(temp_db):
    sponsor_id = temp_db.create_known_sponsor(name='Wise')
    temp_db.update_known_sponsor(sponsor_id, is_active=0)
    assert _learn(temp_db, 'Wise', WISE_NO_EVIDENCE_AD) is None


def test_multi_word_label_is_unaffected(temp_db):
    text = ("Acme Tools makes sturdy gear. Acme Tools ships fast, so try "
            "Acme Tools today with no code needed.")
    assert _learn(temp_db, 'Acme Tools', text) is not None


def test_seed_single_word_brand_is_unaffected(temp_db):
    text = ("Shopify powers stores of every size. Start selling with "
            "Shopify and grow with Shopify.")
    assert _learn(temp_db, 'Shopify', text) is not None


def test_cue_alone_does_not_create_the_row(temp_db):
    text = ("Sponsored by Wise. Wise makes sending money simple. "
            "Wise has no hidden fees, so try Wise today.")
    assert _learn(temp_db, 'Wise', text) is None
    assert temp_db.get_known_sponsor_by_name('Wise') is None


def test_known_short_brand_needs_no_evidence(temp_db):
    text = "Venmo makes paying friends simple. Open the app, and Venmo does the rest, so try Venmo."
    assert _learn(temp_db, 'Venmo', text) is not None


def test_transcriber_misspelling_of_a_known_brand_needs_no_evidence(temp_db):
    text = "Woop tracks your sleep. Woop shows recovery every morning, so try Woop today."
    assert TextPatternMatcher(db=temp_db)._unbacked_new_brand('Woop', text) is False


def test_pattern_only_sponsor_needs_no_evidence(temp_db, monkeypatch):
    monkeypatch.setattr(temp_db, 'get_active_pattern_sponsors', lambda: {'wise'})
    assert TextPatternMatcher(db=temp_db)._unbacked_new_brand('Wise', WISE_NO_EVIDENCE_AD) is False
