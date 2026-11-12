"""A pattern given network scope carries its feed's network and matches that feed."""
import pytest

from pattern_service import PatternService
from tests.integration.fingerprint_helpers import SLUG, csrf, TEMPLATE  # noqa: E402
from utils.pattern_scope import pattern_passes_scope


def _put(client, pid, body):
    return client.put(f'/api/v1/patterns/{pid}', json=body, headers=csrf(client))


def _network(db, network, override=None):
    fields = {k: v for k, v in (('network_id', network), ('network_id_override', override)) if v}
    if fields:
        db.update_podcast(SLUG, **fields)


def _matches_feed(db, pid):
    pattern = db.get_ad_pattern_by_id(pid)
    return pattern_passes_scope(
        pattern['scope'], pattern['network_id'], pattern['podcast_id'], pattern['source'], set(),
        podcast_id=SLUG, network_id=db.get_effective_network_id(SLUG), podcast_tag_set=set())


def test_put_network_scope_takes_the_feeds_network(app_client, fp_feed, fp_pattern):
    _network(fp_feed, 'net1')
    assert _put(app_client, fp_pattern, {'scope': 'network'}).status_code == 200
    pattern = fp_feed.get_ad_pattern_by_id(fp_pattern)
    assert (pattern['scope'], pattern['network_id']) == ('network', 'net1')
    assert _matches_feed(fp_feed, fp_pattern)


def test_put_network_scope_follows_the_override(app_client, fp_feed, fp_pattern):
    _network(fp_feed, 'net2', override=' net1 ')
    assert _put(app_client, fp_pattern, {'scope': 'network'}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['network_id'] == 'net1'


def test_put_network_scope_accepts_an_explicit_network(app_client, fp_feed, fp_pattern):
    _network(fp_feed, 'net1')
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': ' net9 '}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['network_id'] == 'net9'


def test_put_network_scope_without_any_network_is_a_400(app_client, fp_feed, fp_pattern):
    resp = _put(app_client, fp_pattern, {'scope': 'network'})
    assert resp.status_code == 400 and 'network' in resp.get_json()['error'].lower()
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['scope'] == 'podcast'


def test_put_network_scope_without_a_podcast_or_network_is_a_400(app_client, fp_feed):
    pid = fp_feed.create_ad_pattern(scope='global', text_template=TEMPLATE)
    assert _put(app_client, pid, {'scope': 'network'}).status_code == 400
    assert fp_feed.get_ad_pattern_by_id(pid)['scope'] == 'global'


def test_put_network_scope_without_a_podcast_but_with_a_network(app_client, fp_feed):
    pid = fp_feed.create_ad_pattern(scope='global', text_template=TEMPLATE)
    assert _put(app_client, pid, {'scope': 'network', 'networkId': 'net1'}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(pid)['network_id'] == 'net1'


def test_put_network_scope_rejects_a_blank_or_non_string_network(app_client, fp_feed, fp_pattern):
    _network(fp_feed, 'net1')
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': '  '}).status_code == 400
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': 7}).status_code == 400


def test_put_network_scope_keeps_the_network_a_network_pattern_already_has(app_client, fp_feed):
    _network(fp_feed, 'net1')
    pid = fp_feed.create_ad_pattern(
        scope='network', network_id='net-own', podcast_id=SLUG, text_template=TEMPLATE)
    assert _put(app_client, pid, {'scope': 'network'}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(pid)['network_id'] == 'net-own'
    assert _put(app_client, pid, {'scope': 'network', 'networkId': 'net-new'}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(pid)['network_id'] == 'net-new'


def test_put_network_scope_uses_the_feed_network_when_the_pattern_has_none(app_client, fp_feed):
    _network(fp_feed, 'net1')
    pid = fp_feed.create_ad_pattern(
        scope='network', network_id='   ', podcast_id=SLUG, text_template=TEMPLATE)
    assert _put(app_client, pid, {'scope': 'network'}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(pid)['network_id'] == 'net1'


def test_promotion_keeps_the_network_a_network_pattern_already_has(fp_feed):
    _network(fp_feed, 'net1')
    pid = fp_feed.create_ad_pattern(
        scope='network', network_id='net-own', podcast_id=SLUG, text_template=TEMPLATE)
    assert PatternService(db=fp_feed).promote_pattern(pid, 'network') is True
    assert fp_feed.get_ad_pattern_by_id(pid)['network_id'] == 'net-own'


def test_put_network_scope_rejects_a_network_over_100_characters(app_client, fp_feed, fp_pattern):
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': 'n' * 101}).status_code == 400
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['scope'] == 'podcast'
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': ' ' + 'n' * 100 + ' '}).status_code == 200


def test_put_network_limit_applies_after_trimming_unicode_whitespace(app_client, fp_feed, fp_pattern):
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': '\u00a0' + 'n' * 101}).status_code == 400
    assert _put(app_client, fp_pattern, {'scope': 'network', 'networkId': '\u00a0' + 'n' * 100 + '\u00a0'}).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['network_id'] == 'n' * 100


@pytest.mark.parametrize('scope', ['global', 'podcast'])
def test_put_other_scopes_are_unchanged(app_client, fp_feed, fp_pattern, scope):
    _network(fp_feed, 'net1')
    assert _put(app_client, fp_pattern, {'scope': scope}).status_code == 200
    pattern = fp_feed.get_ad_pattern_by_id(fp_pattern)
    assert (pattern['scope'], pattern['network_id']) == (scope, None)


def test_a_promoted_pattern_matches_its_own_feed(fp_feed, fp_pattern):
    _network(fp_feed, 'net1')
    assert PatternService(db=fp_feed).promote_pattern(fp_pattern, 'network') is True
    pattern = fp_feed.get_ad_pattern_by_id(fp_pattern)
    assert (pattern['scope'], pattern['network_id']) == ('network', 'net1')
    assert _matches_feed(fp_feed, fp_pattern)


def test_promotion_without_a_network_changes_nothing(fp_feed, fp_pattern):
    assert PatternService(db=fp_feed).promote_pattern(fp_pattern, 'network') is False
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['scope'] == 'podcast'


def test_promotion_to_global_leaves_the_network_alone(fp_feed, fp_pattern):
    _network(fp_feed, 'net1')
    assert PatternService(db=fp_feed).promote_pattern(fp_pattern, 'global') is True
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['network_id'] is None


def test_a_promotion_merge_keeps_the_network(fp_feed):
    neutral = 'quarterly widget discount offer ' * 8
    _network(fp_feed, 'net-merge')
    mine = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=neutral)
    twin = fp_feed.create_ad_pattern(scope='network', network_id='net-merge', text_template=neutral)
    elsewhere = fp_feed.create_ad_pattern(scope='network', network_id='net-other', text_template=neutral)
    assert PatternService(db=fp_feed).promote_pattern(mine, 'network') is True
    merged = fp_feed.get_ad_patterns(scope='network', network_id='net-merge')
    assert len(merged) == 1 and merged[0]['id'] not in (mine, twin)
    assert fp_feed.get_ad_pattern_by_id(elsewhere)['is_active']
    for pid in (merged[0]['id'], mine, twin, elsewhere):
        fp_feed.delete_ad_pattern(pid)


def test_import_trims_the_network(app_client, fp_feed):
    body = {'patterns': [{'scope': 'network', 'network_id': '  net-import ', 'text_template': TEMPLATE,
                          'sponsor': 'Acme'}], 'mode': 'supplement'}
    resp = app_client.post('/api/v1/patterns/import', json=body, headers=csrf(app_client))
    assert resp.status_code == 200
    imported = fp_feed.get_ad_patterns(scope='network', network_id='net-import')
    assert len(imported) == 1
    fp_feed.delete_ad_pattern(imported[0]['id'])


def test_cue_template_queries_ignore_blank_padded_overrides(fp_feed):
    mine = fp_feed.get_podcast_by_slug(SLUG)
    fp_feed.update_podcast(SLUG, network_id='net1', network_id_override='   ')
    other = 'other-feed'
    fp_feed.create_podcast(other, 'https://example.com/other.xml', 'Other')
    fp_feed.update_podcast(other, network_id='net1')
    try:
        assert [f['slug'] for f in fp_feed.feeds_sharing_network('net1', mine['id'])] == [other]
        mine_override = fp_feed.get_podcast_by_slug(other)
        fp_feed.update_podcast(other, network_id_override='  net1 ')
        assert [f['slug'] for f in fp_feed.feeds_sharing_network('net1', mine['id'])] == [other]
        template = fp_feed.create_cue_template(
            podcast_id=mine_override['id'], cue_type='intro', source_episode_id=None,
            source_offset_s=0.0, duration_s=1.0, sample_rate=8000, n_coeffs=1, mfcc_blob=b'\0',
            scope='network', network_id='net1')
        assert template in [t['id'] for t in fp_feed.list_active_cue_templates_for_feed(mine['id'])]
        assert template in [t['id'] for t in fp_feed.list_cue_templates_for_feed_ui(mine['id'])]
    finally:
        fp_feed.delete_podcast(other)
