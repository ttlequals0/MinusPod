"""A foreign single-word brand counts as a second advertiser only when the text backs it as a brand."""
from text_pattern_matcher import TextPatternMatcher

SPONSOR = 'Globex Corp'
GLOBEX_READ = 'Globex Corp makes sturdy gear. Globex Corp ships fast. '


def _foreign(db, text, sponsor=SPONSOR):
    return TextPatternMatcher(db=db)._contaminating_brands(text, sponsor)


def test_common_word_row_does_not_flag_a_clean_read(temp_db):
    temp_db.create_known_sponsor(name='Day')
    text = GLOBEX_READ + 'Enjoy 365 day returns, and 365 day returns on all orders.'
    assert _foreign(temp_db, text) == []


def test_wise_row_without_evidence_does_not_flag(temp_db):
    temp_db.create_known_sponsor(name='Wise')
    text = GLOBEX_READ + 'The wise women in your life choose wise gear.'
    assert _foreign(temp_db, text) == []


def test_wise_row_with_a_domain_still_flags(temp_db):
    temp_db.create_known_sponsor(name='Wise')
    text = GLOBEX_READ + 'Wise is next. Send money at wise.com, that is wise.com today.'
    assert _foreign(temp_db, text) == ['Wise']


def test_wise_row_with_a_promo_code_still_flags(temp_db):
    temp_db.create_known_sponsor(name='Wise')
    text = GLOBEX_READ + 'Wise is next. Use code WISE for a free transfer with Wise.'
    assert _foreign(temp_db, text) == ['Wise']


def test_single_word_alias_needs_evidence_too(temp_db):
    temp_db.create_known_sponsor(name='Wise Transfers', aliases=['Wise'])
    clean = GLOBEX_READ + 'The wise women in your life choose wise gear.'
    backed = GLOBEX_READ + 'Wise is next. Visit wise.com, that is wise.com today.'
    assert _foreign(temp_db, clean) == []
    assert _foreign(temp_db, backed) == ['Wise Transfers']


def test_multi_word_name_keeps_the_two_mention_rule(temp_db):
    temp_db.create_known_sponsor(name='Acme Tools')
    text = GLOBEX_READ + 'Acme Tools is next. Acme Tools has great deals.'
    assert _foreign(temp_db, text) == ['Acme Tools']


def test_multi_word_name_named_once_is_not_flagged(temp_db):
    temp_db.create_known_sponsor(name='Acme Tools')
    assert _foreign(temp_db, GLOBEX_READ + 'We also saw Acme Tools once.') == []


def test_cue_introduced_single_word_name_flags(temp_db):
    temp_db.create_known_sponsor(name='Wise')
    text = GLOBEX_READ + 'Support comes from Wise, the smart way to send money. Choose the smart way, Wise.'
    assert _foreign(temp_db, text) == ['Wise']


def test_support_for_phrase_cue_flags(temp_db):
    temp_db.create_known_sponsor(name='Wise')
    text = GLOBEX_READ + 'Support for this show comes from the Wise team. Send money with Wise.'
    assert _foreign(temp_db, text) == ['Wise']


def test_and_by_cue_flags(temp_db):
    temp_db.create_known_sponsor(name='Acme')
    text = GLOBEX_READ + 'And by Acme. Get the most out of Acme today.'
    assert _foreign(temp_db, text) == ['Acme']


def test_thanks_to_is_not_a_cue(temp_db):
    temp_db.create_known_sponsor(name='Joe')
    text = GLOBEX_READ + 'Thanks to Joe for the tip. We asked Joe about it.'
    assert _foreign(temp_db, text) == []


def test_cue_for_another_name_does_not_back_the_word(temp_db):
    temp_db.create_known_sponsor(name='Wise')
    text = GLOBEX_READ + 'Sponsored by Initech. The wise women choose wise gear.'
    assert _foreign(temp_db, text) == []
