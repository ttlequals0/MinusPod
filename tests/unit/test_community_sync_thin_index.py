"""Tests for the thin-index incremental sync (#400).

Covers the hash-diff apply gate, the generator/client hash equivalence (the
plan's highest-risk item), the migration backfill, and the per-file fetch cap.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import community_sync  # noqa: E402
from community_sync import apply_manifest  # noqa: E402
from database import Database  # noqa: E402
from utils.community_tags import content_hash_for_bytes  # noqa: E402


@pytest.fixture
def db(tmp_path):
    Database._instance = None  # type: ignore[attr-defined]
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None  # type: ignore[attr-defined]


def _seed_community(db, cid, content_hash=None, version=1, protected=False):
    """Insert a community row directly, optionally with a stored content_hash."""
    pid = db.create_ad_pattern(
        scope='global', text_template=f'{cid} body text for the sponsor dot com promo',
        sponsor_id=db.create_known_sponsor(name=f'Sp-{cid}', aliases=[], category=None),
        source='community', community_id=cid, version=version,
        content_hash=content_hash,
    )
    if protected:
        db.update_ad_pattern(pid, protected_from_sync=1)
    return pid


def _thin_entry(cid, content_hash, version=1):
    return {'community_id': cid, 'content_hash': content_hash, 'path': f'{cid}.json',
            'version': version}


def _fake_file(cid):
    return {'scope': 'global', 'sponsor': f'Sp-{cid}',
            'text_template': f'{cid} fetched body text for the sponsor dot com promo',
            'intro_variants': [], 'outro_variants': []}


def _patch_fetch(monkeypatch):
    calls = []

    def fake_fetch(path):
        calls.append(path)
        cid = path[:-5]  # strip '.json'
        return _fake_file(cid)

    monkeypatch.setattr(community_sync, '_fetch_pattern_file', fake_fetch)
    return calls


def test_thin_index_fetches_only_changed_or_new(db, monkeypatch):
    calls = _patch_fetch(monkeypatch)
    _seed_community(db, 'c1', content_hash='h1')
    manifest = {'manifest_version': 2, 'patterns': [
        _thin_entry('c1', 'h1'),   # unchanged -> skip, no fetch
        _thin_entry('c2', 'h2'),   # new -> fetch + insert
    ]}
    summary = apply_manifest(db, manifest)
    assert summary['inserted'] == 1
    assert summary['skipped'] == 1
    assert summary['updated'] == 0
    assert calls == ['c2.json']  # only the new one was fetched


def test_thin_index_updates_on_hash_change(db, monkeypatch):
    calls = _patch_fetch(monkeypatch)
    _seed_community(db, 'c1', content_hash='h1')
    summary = apply_manifest(db, {'manifest_version': 2,
                                  'patterns': [_thin_entry('c1', 'h2')]})
    assert summary['updated'] == 1
    assert calls == ['c1.json']
    assert db.find_pattern_by_community_id('c1')['content_hash'] == 'h2'


def test_rolled_back_content_resyncs_version_not_a_gate(db, monkeypatch):
    _patch_fetch(monkeypatch)
    _seed_community(db, 'c1', content_hash='h2', version=5)
    # Lower version but a different hash: version-greater-than would skip; the
    # hash gate must re-sync.
    summary = apply_manifest(db, {'manifest_version': 2,
                                  'patterns': [_thin_entry('c1', 'h1', version=2)]})
    assert summary['updated'] == 1
    assert db.find_pattern_by_community_id('c1')['content_hash'] == 'h1'


def test_thin_index_skips_protected_without_fetching(db, monkeypatch):
    calls = _patch_fetch(monkeypatch)
    _seed_community(db, 'c1', content_hash='h1', protected=True)
    summary = apply_manifest(db, {'manifest_version': 2,
                                  'patterns': [_thin_entry('c1', 'h2')]})
    assert summary['skipped'] == 1
    assert calls == []  # protected: never fetched
    assert db.find_pattern_by_community_id('c1')['content_hash'] == 'h1'


def test_migration_backfills_then_stays_stable(db, monkeypatch):
    calls = _patch_fetch(monkeypatch)
    # Pre-migration-style row: community pattern with no content_hash.
    _seed_community(db, 'c1', content_hash=None)
    manifest = {'manifest_version': 2, 'patterns': [_thin_entry('c1', 'h1')]}
    # First sync: None != 'h1' -> one re-fetch to backfill the hash.
    s1 = apply_manifest(db, manifest)
    assert s1['updated'] == 1 and calls == ['c1.json']
    # Second sync, same manifest: hash now stored -> skip, no further fetch.
    s2 = apply_manifest(db, manifest)
    assert s2['skipped'] == 1 and calls == ['c1.json']


def test_generator_client_hash_equivalence(tmp_path):
    """The generator's index content_hash must equal content_hash_for_bytes of
    the published file bytes -- the single shared definition. If these diverge,
    every row re-syncs forever (the plan's #1 risk)."""
    from tools.generate_manifest import _load_pattern_files
    community = tmp_path / 'community'
    community.mkdir()
    for i in range(3):
        doc = {'community_id': f'cid-{i}', 'version': 1, 'sponsor': f'S{i}',
               'submitted_at': '2026-01-01T00:00:00Z', 'text_template': f'body {i}'}
        (community / f'sp{i}-{i}.json').write_text(json.dumps(doc))

    entries = _load_pattern_files(community)
    assert len(entries) == 3
    for e in entries:
        assert 'data' not in e  # thin: no inline body
        raw = (community / e['path']).read_bytes()
        assert e['content_hash'] == content_hash_for_bytes(raw)


def test_fetch_pattern_file_rejects_unsafe_path():
    for bad in ('../secrets.json', 'a/b.json', '..', 'index.txt'):
        with pytest.raises(ValueError):
            community_sync._fetch_pattern_file(bad)


def test_fetch_pattern_file_rejects_oversized(monkeypatch):
    big = b'x' * (community_sync.PATTERN_FILE_MAX_BYTES + 1)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            for i in range(0, len(big), chunk_size):
                yield big[i:i + chunk_size]

        def close(self):
            pass

    monkeypatch.setattr(community_sync, 'safe_get', lambda *a, **k: _FakeResp())
    import requests
    with pytest.raises(requests.RequestException):
        community_sync._fetch_pattern_file('sponsor-1234.json')
