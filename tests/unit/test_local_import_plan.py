"""Tests for the pure planning half of local_import.py: naming parser,
sidecar validation, date synthesis, and the dry-run plan builder."""
import json
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
    ('S001E0005 - Title', ('s01e05', 1, 5, 'Title')),  # wide token, minimal-width id
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


def test_build_import_plan_source_defaults_to_both_and_passes_through(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    default_plan = build_import_plan('myshow', [audio], existing_ids=set(),
                                     overwrite=False, now_iso=NOW_ISO)
    assert default_plan['source'] == 'both'

    audio2 = _write(tmp_path / 'S01E02 - Pilot.mp3')
    explicit_plan = build_import_plan('myshow', [audio2], existing_ids=set(),
                                      overwrite=False, now_iso=NOW_ISO,
                                      source='directory')
    assert explicit_plan['source'] == 'directory'


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


def test_build_import_plan_survives_file_vanishing_before_stat(tmp_path, monkeypatch):
    """A file can vanish between the initial listing pass and the audio.stat()
    call further down the same scan (e.g. a concurrent move/delete). That
    must degrade the single candidate, not raise out of build_import_plan."""
    import pathlib
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    real_stat = pathlib.Path.stat
    calls = {'n': 0}

    def flaky_stat(self, *args, **kwargs):
        if self == audio:
            calls['n'] += 1
            if calls['n'] == 2:
                raise OSError('vanished mid-scan')
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, 'stat', flaky_stat)

    plan = build_import_plan('myshow', [audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)

    assert plan['entries'] == []
    assert plan['totals']['importable'] == 0


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


def test_build_import_plan_wide_token_mints_minimal_width_id(tmp_path):
    """A wide, hand-zero-padded token (s01e0001) with no sidecar override
    still mints the minimal-width id (s01e01) -- the filename is free to
    use any width the naming scheme allows, but the id always normalizes."""
    audio = _write(tmp_path / 'S01E0001 - Pilot.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    entry = plan['entries'][0]
    assert entry['episodeId'] == 's01e01'
    assert entry['audioFile'] == 'S01E0001 - Pilot.mp3'


def test_build_import_plan_wide_token_and_sidecar_override_collide_as_duplicate(tmp_path):
    """A wide-token file (s01e0022, no sidecar season/episode) and a
    narrow-token file whose sidecar overrides to the same numbers (season
    1, episode 22) must mint the identical id and be caught as a
    duplicate within the batch -- before the id canonicalization fix these
    were two different ids (s01e0022 vs s01e22) and neither error fired."""
    wide = _write(tmp_path / 'S01E0022 - Wide.mp3')
    narrow = _write(tmp_path / 'S01E01 - Narrow.mp3')
    _write_text(tmp_path / 'S01E01 - Narrow.json', '{"season": 1, "episode": 22}')
    plan = build_import_plan(
        'myshow', [wide, narrow, tmp_path / 'S01E01 - Narrow.json'],
        existing_ids=set(), overwrite=False, now_iso=NOW_ISO)

    entries_by_file = {e['audioFile']: e for e in plan['entries']}
    assert entries_by_file['S01E0022 - Wide.mp3']['episodeId'] == 's01e22'
    assert entries_by_file['S01E01 - Narrow.mp3']['episodeId'] == 's01e22'
    assert entries_by_file['S01E0022 - Wide.mp3']['errors']
    assert entries_by_file['S01E01 - Narrow.mp3']['errors']
    assert plan['totals']['importable'] == 0


def test_build_import_plan_collision_with_existing_ids_errors_unless_overwrite(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids={'s01e01'},
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['errors']
    assert plan['totals']['importable'] == 0
    # replacesExisting is a collision marker independent of overwrite: this
    # entry DID collide, it just also errored out because overwrite is off.
    assert plan['entries'][0]['replacesExisting'] is True

    audio2 = _write(tmp_path / 'S01E01 - Pilot.mp3')
    plan2 = build_import_plan('myshow', [audio2], existing_ids={'s01e01'},
                               overwrite=True, now_iso=NOW_ISO)
    assert plan2['entries'][0]['errors'] == []
    assert plan2['totals']['importable'] == 1
    assert plan2['entries'][0]['replacesExisting'] is True


def test_build_import_plan_wide_existing_id_collides_with_canonical_rescan(tmp_path):
    """(Review round 2, minor item 4) A pre-fix wide DB id (s01e0006,
    imported before ac2d1eb3's id canonicalization) must still be detected
    as a collision against a fresh rescan of the same episode, which always
    mints the minimal-width id (s01e06) -- both sides of the comparison are
    canonicalized, not just the freshly-scanned candidate."""
    audio = _write(tmp_path / 'S01E06 - Pilot.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids={'s01e0006'},
                              overwrite=False, now_iso=NOW_ISO)
    assert plan['entries'][0]['episodeId'] == 's01e06'
    assert plan['entries'][0]['replacesExisting'] is True
    assert plan['entries'][0]['errors']
    assert plan['totals']['importable'] == 0

    audio2 = _write(tmp_path / 'S01E06 - Pilot.mp3')
    plan2 = build_import_plan('myshow', [audio2], existing_ids={'s01e0006'},
                               overwrite=True, now_iso=NOW_ISO)
    assert plan2['entries'][0]['replacesExisting'] is True
    assert plan2['entries'][0]['errors'] == []
    assert plan2['totals']['importable'] == 1


def test_build_import_plan_replaces_existing_false_when_no_collision(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids=set(),
                              overwrite=True, now_iso=NOW_ISO)
    assert plan['entries'][0]['errors'] == []
    # overwrite=True with nothing to collide with must not claim a
    # replacement -- replacesExisting tracks an actual id collision, not
    # the overwrite flag itself.
    assert plan['entries'][0]['replacesExisting'] is False


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
    even though the final entry itself has no explicit date. Only the two
    offending entries (B and the forced-anchor final C) carry the error --
    an unrelated entry (A) stays clean, per the plan-level-attribution fix."""
    a = _write(tmp_path / 'S01E01 - A.mp3')
    b = _write(tmp_path / 'S01E02 - B.mp3')
    _write_text(tmp_path / 'S01E02 - B.json', '{"published_at": "2030-01-01T00:00:00Z"}')
    c = _write(tmp_path / 'S01E03 - C.mp3')
    sources = [a, b, tmp_path / 'S01E02 - B.json', c]
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                              overwrite=False, now_iso=NOW_ISO)
    assert len(plan['batchErrors']) == 1

    by_audio = {e['audioFile']: e for e in plan['entries']}
    assert by_audio['S01E02 - B.mp3']['errors']
    assert by_audio['S01E03 - C.mp3']['errors']
    assert by_audio['S01E01 - A.mp3']['errors'] == []
    # Only the unrelated entry is (individually) importable; the batch as a
    # whole still can't commit because batchErrors is non-empty.
    assert plan['totals']['importable'] == 1


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


def test_plan_hash_changes_with_overwrite_flag(tmp_path):
    """(round-2 review finding 4) Same exact files, different overwrite ->
    different hash. overwrite changes which entries error out vs. commit
    (a collision is an error with overwrite off, a clean overwrite with it
    on), so a commit whose overwrite doesn't match the reviewed scan must
    409 as stale too -- not just a commit whose files changed. Binding
    overwrite into the hash is what makes that 409 actually fire."""
    a = _write(tmp_path / 'a.mp3')
    sources = [a]
    assert plan_hash(sources, overwrite=False) != plan_hash(sources, overwrite=True)
    # Stable per value, same as the file-only hash already is.
    assert plan_hash(sources, overwrite=True) == plan_hash(sources, overwrite=True)


def test_plan_hash_skips_vanished_path_without_raising(tmp_path):
    """A source that vanishes between listing and stat (e.g. a running
    commit's shutil.move racing a concurrent scan/re-scan) must not raise
    FileNotFoundError out of stat() -- it's dropped from the hash, same
    as build_import_plan's main loop already does for the OSError-on-stat
    case. The resulting hash must differ from the hash computed while the
    file still existed, over the exact same source list: that's what
    makes a commit's re-scan correctly detect staleness (409) rather than
    silently matching a hash that no longer reflects reality."""
    a = _write(tmp_path / 'a.mp3')
    b = _write(tmp_path / 'b.mp3')
    sources = [a, b]

    before = plan_hash(sources)

    b.unlink()  # simulates the commit engine moving b out mid-scan

    after = plan_hash(sources)  # same Path objects; b no longer exists

    assert after != before


# ---------------------------------------------------------------------------
# build_import_plan: batch-level date-order errors (plan-level attribution)
# ---------------------------------------------------------------------------

def test_build_import_plan_batch_error_attributes_only_offending_pair(tmp_path):
    """An out-of-order explicit-date pair must error only the two offending
    entries, not every clean candidate in the batch. The plan also gets a
    top-level batchErrors entry naming them, and an unrelated entry that
    needed synthesis stays clean (errors == [])."""
    e1 = _write(tmp_path / 'S01E01 - One.mp3')
    _write_text(tmp_path / 'S01E01 - One.json',
               json.dumps({'published_at': '2026-02-01T00:00:00Z'}))
    e2 = _write(tmp_path / 'S01E02 - Two.mp3')
    _write_text(tmp_path / 'S01E02 - Two.json',
               json.dumps({'published_at': '2026-01-01T00:00:00Z'}))  # earlier -> out of order
    e3 = _write(tmp_path / 'S01E03 - Three.mp3')

    sources = [e1, tmp_path / 'S01E01 - One.json',
              e2, tmp_path / 'S01E02 - Two.json', e3]
    plan = build_import_plan('myshow', sources, existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)

    assert len(plan['batchErrors']) == 1
    assert 's01e01' in plan['batchErrors'][0]
    assert 's01e02' in plan['batchErrors'][0]

    entries_by_id = {e['episodeId']: e for e in plan['entries']}
    assert entries_by_id['s01e01']['errors'] == plan['batchErrors']
    assert entries_by_id['s01e02']['errors'] == plan['batchErrors']
    # Unrelated entry (needed synthesis, nothing to do with the offending
    # pair): clean, not stamped with an error naming two ids it has no
    # relation to.
    assert entries_by_id['s01e03']['errors'] == []
    # Only the unrelated entry is importable; the whole batch still can't
    # commit (batchErrors non-empty) -- that gate is enforced by the caller.
    assert plan['totals']['importable'] == 1


def test_build_import_plan_no_batch_errors_when_dates_are_fine(tmp_path):
    audio = _write(tmp_path / 'S01E01 - Pilot.mp3')
    plan = build_import_plan('myshow', [audio], existing_ids=set(),
                             overwrite=False, now_iso=NOW_ISO)
    assert plan['batchErrors'] == []
