"""Smoke tests for scripts/compare_fingerprint_matchers.py; no git, fpcalc or network."""
import importlib.util
import json
import random
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import audio_fingerprinter as af

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'compare_fingerprint_matchers.py'
spec = importlib.util.spec_from_file_location('compare_fingerprint_matchers', SCRIPT)
cmp = importlib.util.module_from_spec(spec)
sys.modules['compare_fingerprint_matchers'] = cmp
spec.loader.exec_module(cmp)

IPS = 8.0
SLUG, EPID = 'secret-show', 'abcdef123456'


def _ints(rng, n):
    return [rng.getrandbits(32) - (1 << 31) for _ in range(n)]


def _fake_old(matches):
    class Old:
        def __init__(self, db=None):
            pass

        def _find_matches_fast(self, *_a):
            return list(matches)

        def find_matches(self, *_a):
            return list(matches)

    return SimpleNamespace(AudioFingerprinter=Old)


def _build(tmp_path, monkeypatch):
    rng = random.Random(7)
    pat1, pat2 = _ints(rng, 160), _ints(rng, 160)
    ep = _ints(rng, int(120 * IPS))
    ep[400:560] = pat1
    db = tmp_path / 'copy.db'
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE settings (key TEXT, value TEXT);
        CREATE TABLE podcasts (id INTEGER PRIMARY KEY, slug TEXT, network_id TEXT,
            network_id_override TEXT, tags TEXT);
        CREATE TABLE episodes (id INTEGER PRIMARY KEY, podcast_id INTEGER, episode_id TEXT);
        CREATE TABLE episode_details (episode_id INTEGER, ad_markers_json TEXT);
        CREATE TABLE known_sponsors (id INTEGER PRIMARY KEY, name TEXT, segment_category TEXT,
            tags TEXT, is_active INTEGER);
        CREATE TABLE ad_patterns (id INTEGER PRIMARY KEY, scope TEXT, network_id TEXT, podcast_id TEXT,
            source TEXT, sponsor_id INTEGER, category TEXT, is_active INTEGER);
        CREATE TABLE audio_fingerprints (pattern_id INTEGER, fingerprint BLOB, duration REAL, enabled INTEGER);
        INSERT INTO settings VALUES ('fingerprint_match_strictness', '0.5');
        INSERT INTO podcasts VALUES (1, 'secret-show', NULL, NULL, '[]');
        INSERT INTO episodes VALUES (1, 1, 'abcdef123456');
        INSERT INTO episode_details VALUES (1, '[{"start": 50.0, "end": 70.0}]');
        INSERT INTO known_sponsors VALUES (1, 'Acme', NULL, '[]', 1);
        INSERT INTO ad_patterns VALUES (1, 'global', NULL, NULL, 'local', 1, NULL, 1);
        INSERT INTO ad_patterns VALUES (2, 'global', NULL, NULL, 'local', 1, NULL, 1);
    """)
    for pid, pat in ((1, pat1), (2, pat2)):
        c.execute("INSERT INTO audio_fingerprints VALUES (?, ?, 20.0, 1)",
                  (pid, ','.join(map(str, pat)).encode()))
    c.commit()
    c.close()
    root = tmp_path / 'audio'
    (root / SLUG / 'episodes').mkdir(parents=True)
    (root / SLUG / 'episodes' / f'{EPID}-original.mp3').write_bytes(b'x')
    monkeypatch.setattr(af, 'decode_fingerprint_ints', lambda t: [int(x) for x in t.split(',')])
    provider = lambda _p: (ep, 120.0 * len(ep) / (120 * IPS), 120.0)  # noqa: E731
    return db, root, provider


def _args(db, root, out, **kw):
    return SimpleNamespace(db=str(db), audio_root=str(root), out=str(out), old_ref='fake',
                           limit=None, podcast=None, redact=False, **kw)


def _old_match(pid, start, score):
    return SimpleNamespace(pattern_id=pid, start=start, end=start + 20.0, confidence=score)


def _run(tmp_path, monkeypatch, old, redact=False):
    db, root, provider = _build(tmp_path, monkeypatch)
    out = tmp_path / 'out'
    args = _args(db, root, out)
    args.redact = redact
    code = cmp.run_compare(args, old_module=_fake_old(old), fp_provider=provider, log=lambda *_: None)
    return code, out


GOOD = _old_match(1, 50.0, 0.9)


def test_load_old_matcher_uses_private_module_from_given_source():
    mod = cmp.load_old_matcher('fake', source_text="class AudioFingerprinter:\n    def find_matches(self): return 1\n")
    assert mod.__name__ == cmp.OLD_MODULE_NAME
    assert mod.AudioFingerprinter().find_matches() == 1
    assert not Path(mod.__file__).exists()
    assert hasattr(cmp, 'main')


def test_gate_passes_with_pairs_only(tmp_path, monkeypatch):
    code, out = _run(tmp_path, monkeypatch, [GOOD])
    assert code == 0
    s = json.loads((out / 'report.json').read_text())
    assert s['matched_pairs'] == 1 and s['strictness']['clamped_up'] is True
    assert s['patterns']['loaded'] == 2


def test_unexplained_loss_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(af, 'explain_match', lambda *a, **k: {'matched': True, 'rejected_by': None})
    code, out = _run(tmp_path, monkeypatch, [GOOD, _old_match(2, 90.0, 0.72)])
    assert code == 2
    assert len(json.loads((out / 'report.json').read_text())['gate_blocking']) == 1


def test_explained_loss_does_not_block(tmp_path, monkeypatch):
    code, out = _run(tmp_path, monkeypatch, [GOOD, _old_match(2, 90.0, 0.72)])
    s = json.loads((out / 'report.json').read_text())
    assert code == 0 and len(s['gate_explained']) == 1


def test_band_loss_never_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(af, 'explain_match', lambda *a, **k: {'matched': True, 'rejected_by': None})
    code, out = _run(tmp_path, monkeypatch, [GOOD, _old_match(2, 90.0, 0.67)])
    s = json.loads((out / 'report.json').read_text())
    assert code == 0 and len(s['band']) == 1
    assert 'verdict' in (out / 'report.md').read_text()


def test_report_sections_and_notice(tmp_path, monkeypatch):
    _code, out = _run(tmp_path, monkeypatch, [GOOD])
    md = (out / 'report.md').read_text()
    assert 'Do not paste it into public issues' in md
    for section in ('Gate result', 'Scan', 'Patterns', 'Matches', 'Gained matches', 'Kept remainders',
                    'Far from markers', 'Similarity 0.65 to 0.72', 'Patterns matching more than once',
                    'Edge moves', 'Pieces', 'Listen to these', 'Wall time per episode', 'Notes'):
        assert f'## {section}' in md
    assert md.isascii()


def _new(pid, start, end, similarity=0.9, **kw):
    return af.FingerprintMatch(pattern_id=pid, start=start, end=end, confidence=similarity,
                               similarity=similarity, coverage=0.9, contrast_margin=0.3, **kw)


def _summary(tmp_path, monkeypatch, old, new):
    db, root, provider = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(af, 'match_fingerprints', lambda *a, **k: list(new))
    out = tmp_path / 'out'
    code = cmp.run_compare(_args(db, root, out), old_module=_fake_old(old), fp_provider=provider,
                           log=lambda *_: None)
    return code, json.loads((out / 'report.json').read_text()), (out / 'report.md').read_text()


def test_a_loss_covered_by_another_patterns_match_is_superseded_and_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(af, 'explain_match', lambda *a, **k: {'matched': True, 'rejected_by': None})
    code, s, md = _summary(tmp_path, monkeypatch, [_old_match(2, 50.0, 0.9)], [_new(1, 50.0, 70.0)])
    assert code == 0 and not s['gate_blocking']
    assert [r['pattern_id'] for r in s['superseded']] == [2]
    assert '## Superseded losses' in md


def test_kept_remainders_are_counted_and_one_outside_its_match_fails_the_gate(tmp_path, monkeypatch):
    inside = _new(1, 55.0, 70.0, clipped_remainder=True, candidate_span=(50.0, 70.0))
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    code, s, _md = _summary(tmp_path / 'a', monkeypatch, [GOOD], [inside])
    assert code == 0 and s['remainders']['count'] == 1
    assert s['remainders']['seconds'] == pytest.approx(15.0) and s['remainders']['outside_candidate'] == 0
    outside = _new(1, 45.0, 70.0, clipped_remainder=True, candidate_span=(50.0, 70.0))
    code, s, md = _summary(tmp_path / 'b', monkeypatch, [GOOD], [outside])
    assert code == 2 and s['remainders']['outside_candidate'] == 1 and not s['gate_passed']
    assert '## Kept remainders' in md


def test_far_near_band_and_repeated_matches_are_listed_with_their_contrast_margin(tmp_path, monkeypatch):
    new = [_new(1, 50.0, 70.0), _new(1, 105.0, 115.0, similarity=0.68)]
    _code, s, md = _summary(tmp_path, monkeypatch, [GOOD], new)
    assert [(r['start'], r['marker_distance_s']) for r in s['far_from_markers']] == [(105.0, 35.0)]
    assert [r['start'] for r in s['near_band']] == [105.0]
    assert [r['pattern_matches_in_episode'] for r in s['repeated']] == [2, 2]
    assert all(r['contrast_margin'] == 0.3 for r in s['repeated'])
    for section in ('Far from markers', 'Similarity 0.65 to 0.72', 'Patterns matching more than once'):
        assert f'## {section}' in md


def test_redact_hides_identifiers(tmp_path, monkeypatch):
    _code, out = _run(tmp_path, monkeypatch, [GOOD, _old_match(2, 90.0, 0.72)], redact=True)
    text = (out / 'report.md').read_text() + (out / 'report.json').read_text()
    assert SLUG not in text and EPID not in text and 'Acme' not in text
    assert 'podcast-' in text


def test_database_is_read_only(tmp_path, monkeypatch):
    db, _root, _p = _build(tmp_path, monkeypatch)
    conn = cmp.open_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO settings VALUES ('x', 'y')")


def test_refuses_repo_out_and_live_data(tmp_path):
    with pytest.raises(SystemExit):
        cmp.check_out_dir(cmp.REPO_ROOT / 'report')
    with pytest.raises(SystemExit):
        cmp.check_db_path(cmp.REPO_ROOT / 'data' / 'podcast.db')
    cmp.check_out_dir(tmp_path)


def _default_ref_present():
    import subprocess
    probe = subprocess.run(['git', '-C', str(cmp.REPO_ROOT), 'rev-parse', '--verify', '--quiet',
                            f'{cmp.DEFAULT_OLD_REF}:src/audio_fingerprinter.py'], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _default_ref_present(), reason='default old ref is not in this clone')
def test_default_old_ref_loads_and_matches_a_planted_fingerprint():
    import config
    had = hasattr(config, 'FINGERPRINT_CHUNK_SIZE')
    old = cmp.load_old_matcher()
    assert hasattr(config, 'FINGERPRINT_CHUNK_SIZE') == had
    rng = random.Random(7)
    ep = _ints(rng, int(300 * IPS))
    pat = _ints(rng, 80)
    ep[800:880] = pat
    found = old.AudioFingerprinter(db=None)._find_matches_fast(
        ep, 300.0, [(1, pat, 10.0, None, None)], 300.0, 600, None)
    assert [m.pattern_id for m in found] == [1]
