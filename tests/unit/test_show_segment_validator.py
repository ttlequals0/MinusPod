"""CI checks for show-bound segment files and the manifest's `show_segments` key (synthetic fake-show entries)."""
import copy
import json
import shutil
import uuid
from pathlib import Path

import pytest

import community_segments_core as cs
from tools import generate_manifest
from tools import show_segment_validator as v

GUID = '3f1c9a2e-0b7d-5e44-9c1a-7d2e8f6a1b30'
REPO = Path(__file__).resolve().parents[2]
ASK = ("if you are enjoying the copper kettle hour leave us a review on your podcast app and follow the show "
       "so you never miss an episode and tell a friend who loves tea")


def _entry(category='interaction', short_ask=False, text=ASK, guid=GUID):
    core = cs.stable_core([text] * 5, short_ask=short_ask)
    return cs.entry_body(core, guid, 'podcast_guid', category=category, short_ask=short_ask,
                         durations=[9.0, 10.0, 12.0], language='en', confirms=5, episodes=5, span_days=21,
                         community_id=str(uuid.uuid4()), submitted_at='2026-11-09T00:00:00Z',
                         app_version='2.101.34')


def test_a_well_formed_entry_passes():
    assert v.entry_problems(_entry()) == []
    assert v.entry_problems(_entry('intro')) == []


@pytest.mark.parametrize('field,value', [('text_template', 'leave us a review'), ('sponsor', 'Acme'),
                                         ('podcast', 'kettle')])
def test_plaintext_fields_are_rejected(field, value):
    entry = _entry()
    entry[field] = value
    assert any('unexpected fields' in p for p in v.entry_problems(entry))


def test_any_at_sign_is_rejected():
    entry = _entry()
    entry['source_language'] = 'en@kettle'
    assert any("'@'" in p for p in v.entry_problems(entry))


def test_sizes_per_shape():
    small = _entry()
    small['core'] = small['core'][:11]
    small['head'], small['tail'] = small['core'][:6], small['core'][-6:]
    assert any('needs 12-400' in p for p in v.entry_problems(small))
    short = _entry(short_ask=True, text='please rate and review the copper kettle hour today')
    assert v.entry_problems(short) == []
    too_long = _entry(short_ask=True, text=ASK + ' and while you are there check the full archive of our '
                      'past seasons including the winter specials about brewing and roasting and the summer '
                      'series about iced tea and cold brew recipes from listeners around the world')
    assert any('needs 4-40' in p for p in v.entry_problems(too_long))


@pytest.mark.parametrize('category,needle', [('sponsor', 'sponsor stays sponsor'), ('chitchat', 'category must be'),
                                             ('recap', 'category must be')])
def test_unknown_sponsor_and_recap_categories_are_rejected(category, needle):
    entry = _entry()
    entry['category'] = category
    assert any(needle in p for p in v.entry_problems(entry))


def test_recap_is_accepted_by_receivers_only():
    entry = _entry('recap')
    assert v.entry_problems(entry, library=False) == []
    assert v.entry_problems(entry) != []


def test_short_ask_outside_interaction_and_self_promo_is_rejected():
    entry = _entry('cross_promo', short_ask=True, text='go listen to the brass teapot hour wherever you listen')
    assert any('short_ask is only valid' in p for p in v.entry_problems(entry))


def test_cluster_cap_of_six_per_bucket_and_category():
    base = _entry()
    clones = []
    for _ in range(7):
        clone = copy.deepcopy(base)
        clone['community_id'] = str(uuid.uuid4())
        clones.append(clone)
    assert v.cluster_problems(clones[:6]) == []
    assert any('exceed the cap' in p for p in v.cluster_problems(clones))


def test_validate_files_checks_names_and_bodies(tmp_path):
    entry = _entry()
    good = tmp_path / f"seg-{entry['community_id'].replace('-', '')[:8]}.json"
    good.write_text(json.dumps(entry))
    bad = tmp_path / 'kettle-ask.json'
    bad.write_text(json.dumps(entry))
    report = v.validate_files([good, bad], existing=[])
    assert report[str(good)] == []
    assert 'file name must be' in report[str(bad)][0]


# ---------------------------------------------------------------- manifest key (H9)

def _run_generator(monkeypatch, tmp_path, with_segments):
    community = tmp_path / 'patterns' / 'community'
    shutil.copytree(REPO / 'patterns' / 'community', community)
    if with_segments:
        seg_dir = tmp_path / 'patterns' / 'show-segments'
        seg_dir.mkdir()
        entry = _entry()
        (seg_dir / f"seg-{entry['community_id'].replace('-', '')[:8]}.json").write_text(json.dumps(entry))
        (seg_dir / 'README.md').write_text('not json')
        nested = seg_dir / 'nested'
        nested.mkdir()
        (nested / 'seg-00000000.json').write_text(json.dumps(_entry()))
    monkeypatch.setattr(generate_manifest, '_community_dir', lambda: community)
    monkeypatch.setattr(generate_manifest, '_REPO_SRC', tmp_path / 'src')
    assert generate_manifest.main() == 0
    return (community / 'index.json').read_text(encoding='utf-8')


def test_index_is_byte_identical_without_segment_files(monkeypatch, tmp_path):
    golden = (REPO / 'patterns' / 'community' / 'index.json').read_text(encoding='utf-8')
    regenerated = _run_generator(monkeypatch, tmp_path, with_segments=False)
    assert regenerated == golden


def test_segment_files_go_under_their_own_key_non_recursively(monkeypatch, tmp_path):
    manifest = json.loads(_run_generator(monkeypatch, tmp_path, with_segments=True))
    assert len(manifest['show_segments']) == 1
    item = manifest['show_segments'][0]
    assert set(item) == {'community_id', 'version', 'content_hash', 'path'}
    assert item['path'].startswith('seg-')
