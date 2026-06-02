"""Tests for the bundle splitter."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tools.split_bundle import split  # noqa: E402
from utils.community_tags import BUNDLE_FORMAT, BUNDLE_VERSION  # noqa: E402


def _bundle(*patterns):
    return {
        'format': BUNDLE_FORMAT,
        'bundle_version': BUNDLE_VERSION,
        'submitted_at': '2026-05-28T00:00:00+00:00',
        'submitted_app_version': '2.5.33',
        'pattern_count': len(patterns),
        'patterns': list(patterns),
    }


def _pattern(sponsor, community_id):
    return {
        'scope': 'global',
        'text_template': f'{sponsor} is great, visit {sponsor.lower()}.com today for 10% off.',
        'intro_variants': [],
        'outro_variants': [],
        'avg_duration': None,
        'sponsor': sponsor,
        'sponsor_aliases': [],
        'sponsor_tags': ['universal'],
        'source_language': None,
        'community_id': community_id,
        'version': 1,
        'submitted_at': '2026-05-28T00:00:00+00:00',
        'submitted_app_version': '2.5.33',
        'sponsor_match': 'exact',
    }


def test_split_writes_one_file_per_entry(tmp_path):
    bundle_path = tmp_path / 'minuspod-submission-abc.json'
    bundle_path.write_text(json.dumps(_bundle(
        _pattern('Shopify', '07df78ed-9b7f-4600-a9b7-1aee45b5bfc7'),
        _pattern('Grubhub', '919a1600-268d-4f93-9ae1-73db5f3dab1f'),
    )))
    written = split(bundle_path)
    names = sorted(p.name for p in written)
    assert names == ['grubhub-919a1600.json', 'shopify-07df78ed.json']
    assert not bundle_path.exists(), 'bundle removed by default'


def test_split_keep_original_flag(tmp_path):
    bundle_path = tmp_path / 'minuspod-submission-abc.json'
    bundle_path.write_text(json.dumps(_bundle(
        _pattern('Shopify', '07df78ed-9b7f-4600-a9b7-1aee45b5bfc7'),
    )))
    split(bundle_path, keep_original=True)
    assert bundle_path.exists()


def test_split_refuses_to_overwrite_existing_per_pattern(tmp_path):
    bundle_path = tmp_path / 'minuspod-submission-abc.json'
    bundle_path.write_text(json.dumps(_bundle(
        _pattern('Shopify', '07df78ed-9b7f-4600-a9b7-1aee45b5bfc7'),
    )))
    (tmp_path / 'shopify-07df78ed.json').write_text('{"existing": true}')
    try:
        split(bundle_path)
    except FileExistsError as e:
        assert 'shopify-07df78ed.json' in str(e)
    else:
        raise AssertionError('expected FileExistsError')


def test_split_rejects_non_bundle(tmp_path):
    p = tmp_path / 'not-a-bundle.json'
    p.write_text(json.dumps(_pattern('Shopify', 'abc')))
    try:
        split(p)
    except ValueError as e:
        assert 'bundle' in str(e).lower()
    else:
        raise AssertionError('expected ValueError')


def test_split_rejects_empty_bundle(tmp_path):
    bundle_path = tmp_path / 'minuspod-submission-empty.json'
    bundle_path.write_text(json.dumps(_bundle()))  # no patterns
    try:
        split(bundle_path)
    except ValueError as e:
        assert 'zero patterns' in str(e).lower()
        assert bundle_path.exists(), 'bundle preserved on error'
    else:
        raise AssertionError('expected ValueError')


def test_split_rejects_intra_run_filename_collision(tmp_path):
    # Two entries share a sponsor and the same first UUID segment, so both map
    # to the same filename. The pre-write check must catch the in-run collision
    # (not just on-disk) so neither overwrites the other.
    bundle_path = tmp_path / 'minuspod-submission-abc.json'
    bundle_path.write_text(json.dumps(_bundle(
        _pattern('Shopify', '07df78ed-1111-4600-a9b7-1aee45b5bfc7'),
        _pattern('Shopify', '07df78ed-2222-4600-a9b7-1aee45b5bfc7'),
    )))
    try:
        split(bundle_path)
    except ValueError as e:
        assert 'shopify-07df78ed.json' in str(e)
    else:
        raise AssertionError('expected ValueError on intra-run collision')
    assert not (tmp_path / 'shopify-07df78ed.json').exists(), 'no file written on collision'
    assert bundle_path.exists(), 'bundle untouched on error'


def test_split_atomic_multi_pattern_collision(tmp_path):
    bundle_path = tmp_path / 'minuspod-submission-abc.json'
    bundle_path.write_text(json.dumps(_bundle(
        _pattern('Shopify', '07df78ed-9b7f-4600-a9b7-1aee45b5bfc7'),
        _pattern('Grubhub', '919a1600-268d-4f93-9ae1-73db5f3dab1f'),
    )))
    (tmp_path / 'grubhub-919a1600.json').write_text('{"existing": true}')
    try:
        split(bundle_path)
    except FileExistsError as e:
        assert 'grubhub-919a1600.json' in str(e)
    else:
        raise AssertionError('expected FileExistsError')
    assert not (tmp_path / 'shopify-07df78ed.json').exists(), 'pattern 1 must not be written on collision'
    assert bundle_path.exists(), 'bundle must be untouched on error'
