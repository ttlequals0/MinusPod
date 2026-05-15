"""Tests for the manifest builder (src/tools/generate_manifest.py)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tools.generate_manifest import _load_pattern_files, build_manifest  # noqa: E402


def _write(path: Path, doc):
    path.write_text(json.dumps(doc))


def test_bundle_file_flattens_into_manifest(tmp_path):
    community = tmp_path / 'community'
    community.mkdir()
    _write(community / 'flat.json', {
        'community_id': 'flat-1',
        'version': 1,
        'sponsor': 'A',
        'submitted_at': '2026-01-01T00:00:00Z',
        'text_template': 'flat one',
    })
    _write(community / 'bundle.json', {
        'format': 'minuspod-community-submission',
        'bundle_version': 1,
        'submitted_at': '2026-02-01T00:00:00Z',
        'submitted_app_version': '2.4.5',
        'pattern_count': 2,
        'patterns': [
            {
                'community_id': 'b-1',
                'version': 1,
                'sponsor': 'B',
                'submitted_at': '2026-02-02T00:00:00Z',
                'text_template': 'bundle one',
            },
            {
                'community_id': 'b-2',
                'version': 1,
                'sponsor': 'C',
                'submitted_at': '2026-02-03T00:00:00Z',
                'text_template': 'bundle two',
            },
        ],
    })
    patterns = _load_pattern_files(community)
    ids = {p['community_id'] for p in patterns}
    assert ids == {'flat-1', 'b-1', 'b-2'}
    manifest = build_manifest(patterns)
    manifest_ids = {entry['community_id'] for entry in manifest['patterns']}
    assert manifest_ids == {'flat-1', 'b-1', 'b-2'}


def test_bundle_with_missing_community_id_is_skipped(tmp_path, capsys):
    community = tmp_path / 'community'
    community.mkdir()
    _write(community / 'bundle.json', {
        'format': 'minuspod-community-submission',
        'patterns': [
            {'community_id': 'ok-1', 'sponsor': 'A', 'text_template': 't',
             'version': 1, 'submitted_at': '2026-01-01T00:00:00Z'},
            {'sponsor': 'missing-id', 'text_template': 't'},
        ],
    })
    patterns = _load_pattern_files(community)
    assert [p['community_id'] for p in patterns] == ['ok-1']
    err = capsys.readouterr().err
    assert 'missing community_id' in err
