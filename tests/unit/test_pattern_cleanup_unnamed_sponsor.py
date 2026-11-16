"""Pattern Cleanup's unnamed_sponsor suggestion: sweep, approve, undo, reject and staleness."""
import pytest

import pattern_cleanup
from pattern_cleanup import SuggestionStateError, apply_suggestion, reject_suggestion, undo_suggestion
from tests.unit.test_pattern_cleanup_service import _pattern

TEXT = "Try the Day fresh produce box, order now at example.com slash fresh for twenty percent off."


def _sweep(db):
    return pattern_cleanup._run_stats_sweep(db, None, 90, {})


def _pending(db, pattern_id=None):
    return [s for s in db.get_cleanup_suggestions(status='pending')
            if s['kind'] == 'unnamed_sponsor' and pattern_id in (None, s['pattern_id'])]


def test_sweep_proposes_for_a_sponsor_absent_from_the_text(temp_db):
    p = _pattern(temp_db, text=TEXT, sponsor='Acme')
    changed = _sweep(temp_db)
    assert (p['id'], 'unnamed_sponsor') in changed
    [suggestion] = _pending(temp_db)
    assert suggestion['payload'] == {'sponsor': 'Acme'}
    assert suggestion['confidence'] == 1.0


@pytest.mark.parametrize('text, name, aliases', [
    ("Try Acme, order now at example.com slash fresh.", 'Acme', ['Ak']),
    ("Try Ak, order now at example.com slash fresh.", 'Acme', ['Ak']),
    ("Order now at acmewidgets.com slash fresh for twenty percent off.", 'Acme Widgets', None),
])
def test_sweep_skips_a_pattern_whose_text_names_the_sponsor(temp_db, text, name, aliases):
    if aliases:
        temp_db.create_known_sponsor(name=name, aliases=aliases)
    p = _pattern(temp_db, text=text, sponsor=name)
    _sweep(temp_db)
    assert _pending(temp_db, p['id']) == []


def test_sweep_keeps_a_pattern_with_a_domain_close_to_the_sponsor_name(temp_db):
    p = _pattern(temp_db,
                 text="Order now at acmewidjets.com slash fresh for twenty percent off.",
                 sponsor='Acme Widgets')
    _sweep(temp_db)
    assert _pending(temp_db, p['id']) == []


def test_sweep_skips_a_sponsorless_pattern(temp_db):
    _pattern(temp_db, text=TEXT, sponsor=None)
    assert _sweep(temp_db) == set()
    assert _pending(temp_db) == []


def test_approve_clears_the_sponsor_and_undo_restores_it(temp_db):
    p = _pattern(temp_db, text=TEXT, sponsor='Acme')
    _sweep(temp_db)
    [suggestion] = _pending(temp_db)
    result = apply_suggestion(temp_db, suggestion['id'])
    assert result['status'] == 'approved'
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['sponsor_id'] is None and row['is_active'] == 1
    undo_suggestion(temp_db, suggestion['id'])
    restored = temp_db.get_ad_pattern_by_id(p['id'])
    assert restored['sponsor_id'] == p['sponsor_id'] and restored['sponsor'] == 'Acme'
    _sweep(temp_db)
    assert _pending(temp_db, p['id']) == []


@pytest.mark.parametrize('change', ['sponsor', 'text'])
def test_approve_is_refused_after_the_pattern_changed(temp_db, change):
    p = _pattern(temp_db, text=TEXT, sponsor='Acme')
    _sweep(temp_db)
    [suggestion] = _pending(temp_db)
    if change == 'sponsor':
        temp_db.update_ad_pattern(p['id'], sponsor_id=temp_db.create_known_sponsor(name='Widgetco'))
    else:
        temp_db.update_ad_pattern(p['id'], text_template=TEXT + ' Limited time only.')
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, suggestion['id'])
    assert temp_db.get_ad_pattern_by_id(p['id'])['sponsor']


def test_reject_is_remembered_until_the_text_changes(temp_db):
    p = _pattern(temp_db, text=TEXT, sponsor='Acme')
    _sweep(temp_db)
    reject_suggestion(temp_db, _pending(temp_db)[0]['id'])
    assert _sweep(temp_db) == set()
    assert _pending(temp_db) == []
    temp_db.update_ad_pattern(p['id'], text_template=TEXT + ' Limited time only.')
    assert (p['id'], 'unnamed_sponsor') in _sweep(temp_db)
    assert len(_pending(temp_db)) == 1


@pytest.mark.parametrize('change', ['text_names_sponsor', 'sponsor_changed_to_named'])
def test_pending_suggestion_goes_away_once_the_pattern_no_longer_qualifies(temp_db, change):
    p = _pattern(temp_db, text=TEXT, sponsor='Acme')
    _sweep(temp_db)
    assert len(_pending(temp_db)) == 1
    if change == 'text_names_sponsor':
        temp_db.update_ad_pattern(p['id'], text_template=TEXT + ' Thanks to Acme.')
    else:
        temp_db.update_ad_pattern(p['id'], sponsor_id=temp_db.create_known_sponsor(name='Day'))
    _sweep(temp_db)
    assert _pending(temp_db) == []
    superseded = temp_db.get_connection().execute(
        "SELECT COUNT(*) FROM pattern_cleanup_suggestions "
        "WHERE kind = 'unnamed_sponsor' AND superseded_at IS NOT NULL").fetchone()[0]
    assert superseded == 1
