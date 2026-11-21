"""Global promotion counts only podcast patterns whose own text names the sponsor."""
import pytest

from pattern_service import PatternService

READ = ("Before you go, Before makes sleep simple. Try Before tonight with the "
        "promo code SHOW and get twenty percent off your first order.")
UNRELATED = ("This episode is brought to you by an assistant that drafts emails "
             "and summarizes documents, so you can get more done every single day.")


@pytest.fixture
def service(temp_db):
    return PatternService(db=temp_db)


def _podcast_pattern(db, sponsor_id, slug, text, confirmations=1):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    pattern_id = db.create_ad_pattern(
        scope='podcast', text_template=text, sponsor_id=sponsor_id,
        podcast_id=slug, created_from_episode_id='a1b2c3d4e5f6')
    db.update_ad_pattern(pattern_id, confirmation_count=confirmations)
    return pattern_id


def _three(db, texts):
    sponsor_id = db.create_known_sponsor(name='Before')
    for index, text in enumerate(texts):
        _podcast_pattern(db, sponsor_id, f'show-{index}', text, index + 1)


def test_texts_without_the_label_do_not_qualify(temp_db, service):
    _three(temp_db, [UNRELATED, UNRELATED + ' Again.', UNRELATED + ' More.'])
    assert service.check_sponsor_global_promotion('Before') is False
    assert service.auto_promote_sponsor_patterns('Before') == 0
    assert temp_db.get_ad_patterns(scope='global') == []


def test_texts_with_the_label_qualify_and_promote(temp_db, service):
    _three(temp_db, [READ, READ + ' Again.', READ + ' More.'])
    assert service.check_sponsor_global_promotion('Before') is True
    assert service.auto_promote_sponsor_patterns('Before') == 1
    assert len(temp_db.get_ad_patterns(scope='global')) == 1


def test_two_matching_and_one_unrelated_text_stay_below_threshold(temp_db, service):
    _three(temp_db, [READ, READ + ' Again.', UNRELATED])
    assert service.check_sponsor_global_promotion('Before') is False


def test_unrelated_text_is_never_the_template(temp_db, service):
    sponsor_id = temp_db.create_known_sponsor(name='Before')
    _podcast_pattern(temp_db, sponsor_id, 'show-0', UNRELATED, confirmations=9)
    for index in (1, 2, 3):
        _podcast_pattern(temp_db, sponsor_id, f'show-{index}', READ + f' Take {index}.')
    assert service.auto_promote_sponsor_patterns('Before') == 1
    [promoted] = temp_db.get_ad_patterns(scope='global')
    assert 'promo code SHOW' in promoted['text_template']


def test_an_alias_in_the_text_counts(temp_db, service):
    sponsor_id = temp_db.create_known_sponsor(name='Acme Tools', aliases=['Acme'])
    for index in range(3):
        _podcast_pattern(temp_db, sponsor_id, f'show-{index}',
                         'Acme makes sturdy gear. Try Acme today, no code needed. ' + str(index))
    assert service.check_sponsor_global_promotion('Acme Tools') is True


def _categorized_three(db, categories):
    sponsor_id = db.create_known_sponsor(name='Before')
    for index, category in enumerate(categories):
        pattern_id = _podcast_pattern(db, sponsor_id, f'show-{index}', READ + f' Take {index}.')
        db.update_ad_pattern(pattern_id, category=category)


def test_disabled_global_pattern_is_not_recreated(temp_db, service):
    _three(temp_db, [READ, READ + ' Again.', READ + ' More.'])
    assert service.auto_promote_sponsor_patterns('Before') == 1
    [global_pattern] = temp_db.get_ad_patterns(scope='global')
    temp_db.update_ad_pattern(global_pattern['id'], is_active=False)
    assert service.auto_promote_sponsor_patterns('Before') == 0
    assert len(temp_db.get_ad_patterns(scope='global', active_only=False)) == 1


def test_shared_category_carries_to_global_pattern(temp_db, service):
    _categorized_three(temp_db, ['cross_promo'] * 3)
    assert service.auto_promote_sponsor_patterns('Before') == 1
    [promoted] = temp_db.get_ad_patterns(scope='global')
    assert promoted['category'] == 'cross_promo'


def test_mixed_categories_are_not_promoted(temp_db, service):
    _categorized_three(temp_db, ['cross_promo', 'cross_promo', 'self_promo'])
    assert service.auto_promote_sponsor_patterns('Before') == 0
    assert temp_db.get_ad_patterns(scope='global') == []


def test_uncategorized_patterns_promote_without_category(temp_db, service):
    _three(temp_db, [READ, READ + ' Again.', READ + ' More.'])
    assert service.auto_promote_sponsor_patterns('Before') == 1
    [promoted] = temp_db.get_ad_patterns(scope='global')
    assert 'category' not in promoted
