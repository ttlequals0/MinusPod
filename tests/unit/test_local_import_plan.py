"""Tests for the pure planning half of local_import.py: naming parser,
sidecar validation, date synthesis, and the dry-run plan builder."""
from datetime import datetime
from pathlib import Path

import pytest

from local_import import (
    parse_basename,
    validate_sidecar,
    synthesize_published_at,
    build_import_plan,
    plan_hash,
)


# ---------------------------------------------------------------------------
# parse_basename
# ---------------------------------------------------------------------------

PARSE_BASENAME_CASES = [
    ('S01E05 - The Beginning', ('s01e05', 1, 5, 'The Beginning')),
    ('s01e05 - The Beginning', ('s01e05', 1, 5, 'The Beginning')),
    ('S001E0005 - Title', ('s001e0005', 1, 5, 'Title')),
    ('S100E01 - Title', ('s100e01', 100, 1, 'Title')),
    ('S01E05', ('s01e05', 1, 5, None)),
    ('s01e05', ('s01e05', 1, 5, None)),
    ('s01e05.The', None),                # non-matching: no ' - ' separator
    ('s1e5 - Title', None),               # season/episode below 2-digit min
    ('S01E05 - ', None),                  # empty title after separator
    ('random-file-name', None),
    ('', None),
]


@pytest.mark.parametrize('stem,expected', PARSE_BASENAME_CASES)
def test_parse_basename(stem, expected):
    assert parse_basename(stem) == expected


# ---------------------------------------------------------------------------
# validate_sidecar
# ---------------------------------------------------------------------------

def test_validate_sidecar_unknown_key_rejected():
    clean, err = validate_sidecar({'title': 'X', 'bogus': 1})
    assert clean is None
    assert 'bogus' in err


def test_validate_sidecar_naive_datetime_rejected():
    clean, err = validate_sidecar({'published_at': '2024-01-01T00:00:00'})
    assert clean is None
    assert 'timezone' in err


def test_validate_sidecar_season_zero_ok():
    clean, err = validate_sidecar({'season': 0})
    assert err is None
    assert clean == {'season': 0}


def test_validate_sidecar_full_valid():
    data = {
        'title': 'Ep Title',
        'description': 'desc',
        'published_at': '2024-01-01T00:00:00+00:00',
        'season': 2,
        'episode': 3,
    }
    clean, err = validate_sidecar(data)
    assert err is None
    assert clean['title'] == 'Ep Title'
    assert clean['description'] == 'desc'
    assert clean['season'] == 2
    assert clean['episode'] == 3
    assert clean['published_at'] == '2024-01-01T00:00:00Z'


def test_validate_sidecar_published_at_z_suffix():
    clean, err = validate_sidecar({'published_at': '2024-06-01T12:30:00Z'})
    assert err is None
    assert clean['published_at'] == '2024-06-01T12:30:00Z'


def test_validate_sidecar_title_too_long_rejected():
    clean, err = validate_sidecar({'title': 'x' * 501})
    assert clean is None
    assert 'title' in err


def test_validate_sidecar_title_empty_rejected():
    clean, err = validate_sidecar({'title': ''})
    assert clean is None
    assert 'title' in err


def test_validate_sidecar_episode_zero_rejected():
    clean, err = validate_sidecar({'episode': 0})
    assert clean is None
    assert 'episode' in err


def test_validate_sidecar_season_negative_rejected():
    clean, err = validate_sidecar({'season': -1})
    assert clean is None
    assert 'season' in err


def test_validate_sidecar_bool_rejected_as_int():
    clean, err = validate_sidecar({'season': True})
    assert clean is None
    assert 'season' in err


@pytest.mark.parametrize('bad', [None, [], 'a string', 42])
def test_validate_sidecar_non_dict_rejected(bad):
    clean, err = validate_sidecar(bad)
    assert clean is None
    assert err


def test_validate_sidecar_empty_dict_ok():
    clean, err = validate_sidecar({})
    assert err is None
    assert clean == {}


# ---------------------------------------------------------------------------
# synthesize_published_at
# ---------------------------------------------------------------------------

NOW_ISO = '2026-08-27T00:00:00Z'


def _entries(*ids):
    return [{'episode_id': eid, 'season': 1, 'episode': i + 1, 'published_at': None}
            for i, eid in enumerate(ids)]


def test_synthesize_all_synthesized_steps_back_one_day():
    entries = _entries('s01e01', 's01e02', 's01e03', 's01e04')
    err = synthesize_published_at(entries, NOW_ISO)
    assert err is None
    assert entries[3]['published_at'] == '2026-08-27T00:00:00Z'
    assert entries[2]['published_at'] == '2026-08-26T00:00:00Z'
    assert entries[1]['published_at'] == '2026-08-25T00:00:00Z'
    assert entries[0]['published_at'] == '2026-08-24T00:00:00Z'


def test_synthesize_explicit_anchors_preserved():
    entries = _entries('s01e01', 's01e02', 's01e03')
    entries[0]['published_at'] = '2026-01-01T00:00:00Z'
    entries[2]['published_at'] = '2026-02-01T00:00:00Z'
    err = synthesize_published_at(entries, NOW_ISO)
    assert err is None
    assert entries[0]['published_at'] == '2026-01-01T00:00:00Z'
    assert entries[2]['published_at'] == '2026-02-01T00:00:00Z'


def test_synthesize_even_spacing_between_anchors_strictly_monotonic():
    entries = _entries('s01e01', 's01e02', 's01e03', 's01e04', 's01e05')
    entries[0]['published_at'] = '2026-01-01T00:00:00Z'
    entries[4]['published_at'] = '2026-01-05T00:00:00Z'
    err = synthesize_published_at(entries, NOW_ISO)
    assert err is None
    # Interval split into 4 equal (1 day) steps.
    assert entries[0]['published_at'] == '2026-01-01T00:00:00Z'
    assert entries[1]['published_at'] == '2026-01-02T00:00:00Z'
    assert entries[2]['published_at'] == '2026-01-03T00:00:00Z'
    assert entries[3]['published_at'] == '2026-01-04T00:00:00Z'
    assert entries[4]['published_at'] == '2026-01-05T00:00:00Z'
    dates = [datetime.fromisoformat(e['published_at'].replace('Z', '+00:00'))
             for e in entries]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)


def test_synthesize_out_of_order_anchors_returns_error_naming_both_ids():
    entries = _entries('s01e01', 's01e02', 's01e03')
    entries[0]['published_at'] = '2026-02-01T00:00:00Z'
    entries[1]['published_at'] = '2026-01-01T00:00:00Z'  # earlier than prior
    err = synthesize_published_at(entries, NOW_ISO)
    assert err is not None
    assert 's01e01' in err
    assert 's01e02' in err


def test_synthesize_equal_explicit_dates_rejected():
    entries = _entries('s01e01', 's01e02')
    entries[0]['published_at'] = '2026-01-01T00:00:00Z'
    entries[1]['published_at'] = '2026-01-01T00:00:00Z'
    err = synthesize_published_at(entries, NOW_ISO)
    assert err is not None
    assert 's01e01' in err and 's01e02' in err


def test_synthesize_single_unset_entry_anchors_at_now():
    entries = _entries('s01e01')
    err = synthesize_published_at(entries, NOW_ISO)
    assert err is None
    assert entries[0]['published_at'] == NOW_ISO


# ---------------------------------------------------------------------------
# build_import_plan
# ---------------------------------------------------------------------------

def _write(path: Path, content: bytes = b'\x00' * 10):
    path.write_bytes(content)
    return path


def _write_text(path: Path, text: str):
    path.write_text(text, encoding='utf-8')
    return path


def test_build_import_plan_basic_matched_entry(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    sources = [audio]
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['slug'] == 'myshow'
    assert plan['overwrite'] is False
    assert len(plan['entries']) == 1
    entry = plan['entries'][0]
    assert entry['episodeId'] == 's01e01'
    assert entry['season'] == 1
    assert entry['episode'] == 1
    assert entry['title'] == 'Pilot'
    assert entry['audioFile'] == 'S01E01 - Pilot.mp3'
    assert entry['errors'] == []
    assert entry['publishedAt'] == NOW_ISO
    assert entry['publishedAtSource'] == 'synthesized'
    assert plan['totals']['importable'] == 1
    assert plan['totals']['rejected'] == 0
    assert plan['totals']['errors'] == 0
    assert plan['totals']['bytes'] == entry['bytes']


def test_build_import_plan_title_fallback_episode_number(tmp_path):
    audio = _write(tmp_path / 'S02E07.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['title'] == 'Episode 7'


def test_build_import_plan_dotfile_rejected(tmp_path):
    dotfile = _write(tmp_path / '.DS_Store')
    plan = build_import_plan('myshow', [dotfile], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'] == []
    assert plan['rejected'] == [{'file': '.DS_Store', 'reason': 'hidden file (dotfile)'}]
    assert plan['totals']['rejected'] == 1


def test_build_import_plan_part_and_tmp_rejected(tmp_path):
    part = _write(tmp_path / 'S01E01 - Pilot.mp3.part')
    tmp = _write(tmp_path / 'S01E02 - Two.mp3.tmp')
    plan = build_import_plan('myshow', [part, tmp], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'] == []
    reasons = {r['file']: r['reason'] for r in plan['rejected']}
    assert 'S01E01 - Pilot.mp3.part' in reasons
    assert 'S01E02 - Two.mp3.tmp' in reasons


def test_build_import_plan_zero_byte_rejected(tmp_path):
    empty = _write(tmp_path / 'S01E01 - Pilot.mp3', b'')
    plan = build_import_plan('myshow', [empty], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'] == []
    assert plan['rejected'][0]['file'] == 'S01E01 - Pilot.mp3'
    assert '0 byte' in plan['rejected'][0]['reason']


def test_build_import_plan_non_mp3_audio_like_rejected_with_ffmpeg_hint(tmp_path):
    wav = _write(tmp_path / 'S01E01 - Pilot.wav')
    plan = build_import_plan('myshow', [wav], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'] == []
    assert len(plan['rejected']) == 1
    assert plan['rejected'][0]['file'] == 'S01E01 - Pilot.wav'
    assert 'ffmpeg' in plan['rejected'][0]['reason']


def test_build_import_plan_non_matching_name_rejected(tmp_path):
    bad = _write(tmp_path / 'random-file.mp3')
    plan = build_import_plan('myshow', [bad], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'] == []
    assert plan['rejected'][0]['file'] == 'random-file.mp3'
    assert 'naming scheme' in plan['rejected'][0]['reason']


def test_build_import_plan_orphan_sidecar_rejected(tmp_path):
    sidecar = _write_text(tmp_path / 'S01E01 - Pilot.json', '{}')
    plan = build_import_plan('myshow', [sidecar], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'] == []
    assert plan['rejected'][0]['file'] == 'S01E01 - Pilot.json'
    assert 'no matching audio' in plan['rejected'][0]['reason']


def test_build_import_plan_duplicate_id_within_batch_errors_both(tmp_path):
    a = _write(tmp_path / 'S01E01 - A.mp3')
    b = _write_text(tmp_path / 'S01E01 - B.json', '{}')
    # Give B its own matching audio too, forcing the same episode id.
    b_audio = _write(tmp_path / 'S01E01 - B.mp3')
    plan = build_import_plan('myshow', [a, b, b_audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    entries_by_file = {e['audioFile']: e for e in plan['entries']}
    assert entries_by_file['S01E01 - A.mp3']['errors']
    assert entries_by_file['S01E01 - B.mp3']['errors']
    assert plan['totals']['importable'] == 0
    assert plan['totals']['errors'] == 2


def test_build_import_plan_collision_with_existing_ids_errors_unless_overwrite(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids={'s01e01'},
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['errors']
    assert plan['totals']['importable'] == 0

    audio2 = _write(tmp_path / 'S01E01 - Pilot.mp3')
    plan2 = build_import_plan('myshow', [audio2], existing_ids={'s01e01'},
                               overwrite=True, now_iso=NOW_ISO)
    assert plan2['entries'][0]['errors'] == []
    assert plan2['totals']['importable'] == 1


def test_build_import_plan_sidecar_overrides_token_and_rechecks_collision(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    _write_text(tmp_path / 'S01E01 - Pilot.json', '{"season": 2, "episode": 9}')
    plan = build_import_plan('myshow', [audio, tmp_path / 'S01E01 - Pilot.json'],
                              existing_ids={'s02e09'}, overwrite=False, now_iso=NOW_ISO)
    entry = plan['entries'][0]
    assert entry['episodeId'] == 's02e09'
    assert entry['season'] == 2
    assert entry['episode'] == 9
    assert entry['errors']  # collides with existing_ids under new id


def test_build_import_plan_invalid_sidecar_rejects_episode_entirely(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    _write_text(tmp_path / 'S01E01 - Pilot.json', '{"bogus": 1}')
    plan = build_import_plan('myshow', [audio, tmp_path / 'S01E01 - Pilot.json'],
                              existing_ids=set(), overwrite=False, now_iso=NOW_ISO)
    entry = plan['entries'][0]
    assert entry['errors']
    assert entry['episodeId'] == 's01e01'  # falls back to filename token
    assert plan['totals']['importable'] == 0


def test_build_import_plan_sidecar_title_and_description_and_artwork(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    _write_text(tmp_path / 'S01E01 - Pilot.json', '{"title": "Custom Title"}')
    _write_text(tmp_path / 'S01E01 - Pilot.txt', 'description text')
    _write(tmp_path / 'S01E01 - Pilot.jpg')
    sources = [audio,
               tmp_path / 'S01E01 - Pilot.json',
               tmp_path / 'S01E01 - Pilot.txt',
               tmp_path / 'S01E01 - Pilot.jpg']
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    entry = plan['entries'][0]
    assert entry['title'] == 'Custom Title'
    assert entry['descriptionFile'] == 'S01E01 - Pilot.txt'
    assert entry['artworkFile'] == 'S01E01 - Pilot.jpg'
    assert entry['sidecarFile'] == 'S01E01 - Pilot.json'


def test_build_import_plan_out_of_order_explicit_anchors_error_all_clean_entries(tmp_path):
    """Regression: two explicit sidecar dates out of order must error every
    clean entry in the batch (totals.importable == 0), not silently pass."""
    a = _write(tmp_path / 'S01E01 - A.mp3')
    _write_text(tmp_path / 'S01E01 - A.json', '{"published_at": "2026-02-01T00:00:00Z"}')
    b = _write(tmp_path / 'S01E02 - B.mp3')
    _write_text(tmp_path / 'S01E02 - B.json', '{"published_at": "2026-01-01T00:00:00Z"}')
    sources = [a, tmp_path / 'S01E01 - A.json', b, tmp_path / 'S01E02 - B.json']
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['totals']['importable'] == 0
    for entry in plan['entries']:
        assert entry['errors']


def test_build_import_plan_future_dated_non_final_anchor_errors_batch(tmp_path):
    """Regression: a future-dated explicit sidecar on a NON-final entry must
    still be caught against the implicit now_iso anchor on the final entry,
    even though the final entry itself has no explicit date."""
    a = _write(tmp_path / 'S01E01 - A.mp3')
    b = _write(tmp_path / 'S01E02 - B.mp3')
    _write_text(tmp_path / 'S01E02 - B.json', '{"published_at": "2030-01-01T00:00:00Z"}')
    c = _write(tmp_path / 'S01E03 - C.mp3')
    sources = [a, b, tmp_path / 'S01E02 - B.json', c]
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['totals']['importable'] == 0
    for entry in plan['entries']:
        assert entry['errors']


def test_build_import_plan_duplicate_sidecar_date_does_not_backdate_clean_siblings(tmp_path):
    """Regression: a duplicate (errored, non-committable) entry carrying a
    bogus far-future sidecar date must not act as a synthesis anchor for its
    clean siblings."""
    a = _write(tmp_path / 'S01E01 - A.mp3')
    b1 = _write(tmp_path / 'S01E02 - B.mp3')
    _write_text(tmp_path / 'S01E02 - B.json', '{"published_at": "2030-01-01T00:00:00Z"}')
    b2 = _write(tmp_path / 'S01E02 - C.mp3')  # same sNNeNN token -> duplicate with B
    d = _write(tmp_path / 'S01E03 - D.mp3')
    sources = [a, b1, tmp_path / 'S01E02 - B.json', b2, d]
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)

    by_audio = {e['audioFile']: e for e in plan['entries']}
    assert by_audio['S01E02 - B.mp3']['errors']
    assert by_audio['S01E02 - C.mp3']['errors']

    clean_a = by_audio['S01E01 - A.mp3']
    clean_d = by_audio['S01E03 - D.mp3']
    assert clean_a['errors'] == []
    assert clean_d['errors'] == []
    # D is the final clean entry -> anchors at now_iso; A leads it by 1 day.
    # Neither is anywhere near the duplicate's bogus 2030 date.
    assert clean_d['publishedAt'] == NOW_ISO
    assert clean_a['publishedAt'] == '2026-08-26T00:00:00Z'


def test_build_import_plan_hash_matches_plan_hash_fn(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    sources = [audio]
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['planHash'] == plan_hash(sources)


# ---------------------------------------------------------------------------
# plan_hash
# ---------------------------------------------------------------------------

def test_plan_hash_stable_across_calls(tmp_path):
    a = _write(tmp_path / 'a.mp3')
    b = _write(tmp_path / 'b.mp3')
    sources = [a, b]
    assert plan_hash(sources) == plan_hash(sources)


def test_plan_hash_changes_when_file_touched(tmp_path):
    a = _write(tmp_path / 'a.mp3')
    sources = [a]
    before = plan_hash(sources)
    # Change content and mtime so both size/mtime_ns differ.
    a.write_bytes(b'\x00' * 20)
    os_stat_time_bump(a)
    after = plan_hash(sources)
    assert before != after


def os_stat_time_bump(path: Path):
    import os
    import time
    time.sleep(0.01)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns + 10_000_000, st.st_mtime_ns + 10_000_000))


def test_plan_hash_order_independent(tmp_path):
    a = _write(tmp_path / 'a.mp3')
    b = _write(tmp_path / 'b.mp3')
    assert plan_hash([a, b]) == plan_hash([b, a])
