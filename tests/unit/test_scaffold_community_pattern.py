"""Tests for the manual-contributor scaffold tool."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tools.scaffold_community_pattern import scaffold  # noqa: E402


def test_scaffold_writes_named_file(tmp_path):
    out = scaffold(
        sponsor='Shopify',
        out_dir=tmp_path,
        text_template='Shopify is the commerce platform behind millions of businesses.',
        tags=['universal', 'business'],
        aliases=[],
    )
    assert out.name.startswith('shopify-')
    assert out.name.endswith('.json')
    data = json.loads(out.read_text())
    assert data['sponsor'] == 'Shopify'
    assert data['sponsor_tags'] == ['universal', 'business']
    assert data['version'] == 1
    assert data['scope'] == 'global'
    assert data['community_id']
    # short uuid in filename equals first segment of community_id
    short = out.stem.rsplit('-', 1)[-1]
    assert data['community_id'].startswith(short)


def test_scaffold_refuses_overwrite(tmp_path):
    scaffold(sponsor='Shopify', out_dir=tmp_path, text_template='x' * 60, tags=['universal'])
    first = scaffold(
        sponsor='Shopify',
        out_dir=tmp_path,
        text_template='x' * 60,
        tags=['universal'],
        community_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    )
    try:
        scaffold(
            sponsor='Shopify',
            out_dir=tmp_path,
            text_template='y' * 60,
            tags=['universal'],
            community_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        )
    except FileExistsError as e:
        assert first.name in str(e)
    else:
        raise AssertionError('expected FileExistsError')


def test_scaffold_force_overwrites(tmp_path):
    a = scaffold(sponsor='Shopify', out_dir=tmp_path, text_template='x' * 60,
                 tags=['universal'], community_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
    b = scaffold(sponsor='Shopify', out_dir=tmp_path, text_template='y' * 60,
                 tags=['universal'], community_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                 force=True)
    assert a == b
    assert 'y' * 60 in b.read_text()
