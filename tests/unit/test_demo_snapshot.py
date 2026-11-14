import sqlite3
from datetime import date
from unittest.mock import patch

import pytest

from scripts.demo.export_allowlist import create_full_schema, SETTINGS_FORCE, SETTINGS_FORCE_UNREGISTERED
from scripts.demo.snapshot import SnapshotError, exclude_feeds, main, snapshot

HOSTS = 'pods.example.com'


def make_db(path):
    built = sqlite3.connect(create_full_schema(path.parent / 'built'))
    conn = sqlite3.connect(path)
    built.backup(conn)
    built.close()
    conn.executemany("INSERT INTO podcasts (id, slug, source_url, title, created_at, updated_at) VALUES (?,?,?,?,?,?)", [
        (1, 'keep', 'https://feeds.example.net/keep', 'Keep', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
        (2, 'zzexcluded', 'https://www.patreon.com/rss/paid?auth=tok', 'Zed', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
    ])
    conn.executemany("INSERT INTO episodes (podcast_id, episode_id, original_url, title, status, published_at, created_at, updated_at, processed_file) VALUES (?,?,?,?,?,?,?,?,?)", [
        (1, 'new', 'https://cdn/new.mp3', 'New', 'processed', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z', 'episodes/new.mp3'),
        (1, 'old', 'https://cdn/old.mp3', 'Old', 'processed', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', 'episodes/old.mp3'),
        (2, 'p1', 'https://cdn/p1.mp3', 'P1', 'processed', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z', None),
    ])
    conn.execute("DELETE FROM settings")
    conn.executemany("INSERT INTO settings (key, value) VALUES (?,?)", [
        ('anthropic_api_key', 'enc:v1:aaa:bbb'), ('flask_secret_key', 'zzz'), ('system_prompt', 'Find ads'), ('feed_auth_enabled', 'true'),
    ])
    conn.executemany("INSERT INTO episode_downloads (podcast_id, episode_id, client_ip, user_agent, first_seen_at, last_seen_at, request_count) VALUES (?,?,?,?,?,?,?)", [
        (1, 'new', '8.8.8.8', 'PocketCasts', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z', 1),
        (1, 'new', '10.0.0.5', 'Overcast', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z', 1),
        (2, 'p1', '1.1.1.1', 'X', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z', 1),
    ])
    conn.executemany("INSERT INTO ip_geo_cache (ip, country, region, city, looked_up_at, status) VALUES (?,?,?,?,?,?)", [
        ('8.8.8.8', 'US', 'CA', 'Mountain View', '2026-10-21T00:00:00Z', 'ok'),
    ])
    conn.execute("INSERT INTO processing_history (podcast_id, podcast_slug, episode_id, processed_at, status, log_file) VALUES (1,'keep','new','2026-10-20T01:00:00Z','completed','/app/data/logs/x.log')")
    conn.execute("INSERT INTO ad_patterns (id, scope, podcast_id, text_template) VALUES (1, 'podcast', 'zzexcluded', 'ad one')")
    conn.execute("INSERT INTO ad_patterns (id, scope, podcast_id, text_template) VALUES (2, 'global', 'zzexcluded', 'ad two')")
    conn.execute("INSERT INTO pattern_corrections (pattern_id, correction_type, origin) VALUES (1, 'false_positive', 'user')")
    conn.execute("INSERT INTO ad_reviewer_log (episode_id, podcast_id, pass, pool, original_start, original_end, verdict, model_used, success, created_at) VALUES ('p1','zzexcluded',1,'x',0,1,'v','m',1,'2026-10-20 00:00:00')")
    conn.execute("INSERT INTO audio_fingerprints (pattern_id) VALUES (1)")
    conn.execute(
        "INSERT INTO audio_fingerprints (pattern_id, source_podcast_slug, source_episode_id, source_start, source_end)"
        " VALUES (2, 'zzexcluded', 'p1', 10.0, 40.0)"
    )
    conn.execute(
        "INSERT INTO audio_fingerprint_matches (pattern_id, episode_id, podcast_slug, match_start, match_end, in_scope)"
        " VALUES (2, 'p1', 'zzexcluded', 10.0, 40.0, 1)"
    )
    conn.execute("INSERT INTO addressing_log (created_at, podcast_slug, episode_id, pass_name, configured_mode, effective_mode, windows_judged, windows_compliant) VALUES ('2026-08-01T00:00:00Z','keep','old','p1','a','a',1,1)")
    conn.execute("INSERT INTO addressing_log (created_at, podcast_slug, episode_id, pass_name, configured_mode, effective_mode, windows_judged, windows_compliant) VALUES ('2026-10-20T00:00:00Z','keep','new','p1','a','a',1,1)")
    conn.execute("INSERT INTO ad_reviewer_log (episode_id, podcast_id, pass, pool, original_start, original_end, verdict, model_used, success, created_at) VALUES ('old','keep',1,'x',0,1,'v','m',1,'2026-10-20 00:00:00')")
    conn.commit()
    conn.close()


def test_snapshot_excludes_windows_scrubs_and_shifts(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    report = snapshot(src, out, days=30, excluded_slugs={'zzexcluded'}, hosts=['pods.example.com'], export_day=date(2026, 11, 6), salt=b'salt')
    conn = sqlite3.connect(out)
    assert [r[0] for r in conn.execute("SELECT slug FROM podcasts")] == ['keep']
    assert [r[0] for r in conn.execute("SELECT episode_id FROM episodes")] == ['new']
    assert conn.execute("SELECT COUNT(*) FROM episode_downloads WHERE podcast_id = 2").fetchone()[0] == 0
    forced = {**SETTINGS_FORCE, **SETTINGS_FORCE_UNREGISTERED}
    forced.pop('feed_auth_enabled')
    assert dict(conn.execute("SELECT key, value FROM settings")) == {'system_prompt': 'Find ads', 'feed_auth_enabled': 'false', **forced}
    ips = sorted(r[0] for r in conn.execute("SELECT client_ip FROM episode_downloads"))
    assert '10.0.0.5' in ips and '8.8.8.8' not in ips
    assert conn.execute("SELECT region, city FROM ip_geo_cache").fetchone() == (None, None)
    assert conn.execute("SELECT log_file FROM processing_history").fetchone()[0] is None
    assert conn.execute("SELECT processed_file FROM episodes").fetchone()[0] == 'episodes/new.mp3'
    # newest record 2026-10-21 lands on export day 2026-11-06: +16 days
    assert conn.execute("SELECT last_seen_at FROM episode_downloads LIMIT 1").fetchone()[0] == '2026-11-06T00:00:00Z'
    assert conn.execute("SELECT published_at FROM episodes").fetchone()[0] == '2026-11-05T00:00:00Z'
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='search_index'").fetchone() is None
    assert not (tmp_path / 'snap.db-wal').exists()
    assert report.delta_days == 16
    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprints").fetchone()[0] == 1
    assert conn.execute(
        "SELECT pattern_id, source_podcast_slug, source_episode_id, source_start, source_end FROM audio_fingerprints"
    ).fetchone() == (2, None, None, None, None)
    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprint_matches").fetchone()[0] == 0
    assert conn.execute("SELECT id, podcast_id FROM ad_patterns").fetchall() == [(2, None)]
    assert conn.execute("SELECT COUNT(*) FROM pattern_corrections").fetchone()[0] == 0
    assert b'zzexcluded' not in out.read_bytes()
    assert [r[0] for r in conn.execute("SELECT episode_id FROM addressing_log")] == ['new']
    assert conn.execute("SELECT COUNT(*) FROM ad_reviewer_log").fetchone()[0] == 0


def test_geo_collisions_keep_one_row(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO ip_geo_cache (ip, country, looked_up_at, status) VALUES ('9.9.9.9','DE','2026-10-21T00:00:00Z','ok')")
    conn.execute("INSERT INTO episode_downloads (podcast_id, episode_id, client_ip, user_agent, first_seen_at, last_seen_at, request_count) VALUES (1,'new','9.9.9.9','X','2026-10-21T00:00:00Z','2026-10-21T00:00:00Z',1)")
    conn.commit()
    conn.close()
    with patch('scripts.demo.snapshot.map_ip', side_effect=lambda ip, salt: ip if ip.startswith('10.') else '192.0.2.1'):
        snapshot(src, out, days=30, excluded_slugs={'zzexcluded'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'


def test_deleted_rows_do_not_survive_in_file(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    snapshot(src, out, days=30, excluded_slugs={'zzexcluded'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    assert b'auth=tok' not in out.read_bytes()
    assert b'enc:v1:aaa' not in out.read_bytes()


def test_out_equal_to_source_is_rejected(tmp_path):
    src = tmp_path / 'live.db'
    make_db(src)
    with pytest.raises(ValueError):
        snapshot(src, src, days=30, excluded_slugs={'zzexcluded'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    assert main(['--source', str(src), '--out', str(src), '--exclusions', str(tmp_path / 'ex.json'), '--hosts', HOSTS]) == 1
    assert src.exists()
    assert list(tmp_path.glob('.snapshot-*')) == []


def test_bad_exclusions_json_returns_1(tmp_path):
    src, bad = tmp_path / 'live.db', tmp_path / 'ex.json'
    make_db(src)
    for body in ('[]', '{"slugs": "paid"}', '{"slugs": [1]}'):
        bad.write_text(body)
        assert main(['--source', str(src), '--out', str(tmp_path / 'o.db'), '--exclusions', str(bad), '--hosts', HOSTS]) == 1


def test_fractional_and_offset_timestamps_shift_in_sql_pass(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE episodes SET description = '2026-10-01T00:00:00+00:00' WHERE episode_id = 'new'")
    conn.commit()
    conn.close()
    snapshot(src, out, days=30, excluded_slugs={'zzexcluded'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT description FROM episodes").fetchone()[0] == '2026-10-17T00:00:00+00:00'


def test_leak_check_flags_key_shaped_text_and_main_exits_2(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE podcasts SET description = ? WHERE slug = 'keep'", ('sk-ant-' + 'a1B2c3D4e5' * 3,))
    conn.commit()
    conn.close()
    report = snapshot(src, out, days=30, excluded_slugs={'zzexcluded'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    assert [h.pattern for h in report.leaks] == ['anthropic_key']
    (tmp_path / 'ex.json').write_text('{"slugs": ["zzexcluded"]}')
    out2 = tmp_path / 'o.db'
    assert main(['--source', str(src), '--out', str(out2), '--exclusions', str(tmp_path / 'ex.json'), '--hosts', HOSTS, '--export-day', '2026-11-06']) == 2
    assert not out2.exists()


def test_emails_and_paths_are_rewritten_and_leave_no_leaks(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE podcasts SET description = 'write to host@corp.example today' WHERE slug = 'keep'")
    conn.execute("UPDATE processing_history SET processing_stats_json = ? WHERE episode_id = 'new'", ('{"err": "load /app/data/x failed"}',))
    conn.commit()
    conn.close()
    report = snapshot(src, out, days=30, excluded_slugs={'zzexcluded'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT description FROM podcasts WHERE slug = 'keep'").fetchone()[0] == 'write to contact@example.com today'
    assert conn.execute("SELECT processing_stats_json FROM processing_history").fetchone()[0] == '{"err": "load /demo/x failed"}'
    assert report.leaks == []


def test_excluded_slugs_without_podcast_row_still_clean_patterns(tmp_path):
    src = tmp_path / 'live.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute('DELETE FROM episodes WHERE podcast_id = 2')
    conn.execute('DELETE FROM episode_downloads WHERE podcast_id = 2')
    conn.execute("DELETE FROM podcasts WHERE slug = 'zzexcluded'")
    assert exclude_feeds(conn, {'zzexcluded'}) == 0
    assert conn.execute("SELECT id, podcast_id FROM ad_patterns").fetchall() == [(2, None)]


def seed(conn, table, **vals):
    # fills required columns that have no default so only the keys under test matter
    for _, name, decl, notnull, default, pk in conn.execute(f'PRAGMA table_info({table})').fetchall():
        if name in vals or default is not None or not notnull or name == 'id':
            continue
        vals[name] = 0 if any(t in decl.upper() for t in ('INT', 'REAL')) else ('' if 'BLOB' not in decl.upper() else b'')
    cols = ', '.join(vals)
    conn.execute(f'INSERT INTO {table} ({cols}) VALUES ({", ".join("?" * len(vals))})', list(vals.values()))  # noqa: S608


ID_KEYED = (
    ('processing_history', {'podcast_slug': 'x', 'status': 'completed', 'processed_at': '2026-10-20T00:00:00Z'}),
    ('cue_detections', {}),
    ('cue_candidate_scans', {}),
    ('cue_threshold_scans', {}),
    ('cue_candidate_dismissals', {'source_episode_id': 'x'}),
    ('cue_cross_episode_scans', {}),
    ('llm_call_usage', {}),
)


def test_exclusion_removes_id_keyed_rows_of_the_feed_only(tmp_path):
    src = tmp_path / 'live.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute('DELETE FROM processing_history')
    for table, extra in ID_KEYED:
        for pid in (1, 2):
            vals = {'podcast_id': pid, **extra}
            if table not in ('cue_candidate_dismissals', 'cue_cross_episode_scans'):
                vals['episode_id'] = 'new' if pid == 1 else 'p1'
            seed(conn, table, **vals)
    exclude_feeds(conn, {'zzexcluded'})
    for table, _ in ID_KEYED:
        assert conn.execute(f'SELECT podcast_id FROM {table}').fetchall() == [(1,)], table  # noqa: S608


def test_unmatched_exclusion_slug_is_an_error(tmp_path, capsys):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    with pytest.raises(SnapshotError, match='zzmisspelled'):
        snapshot(src, out, days=30, excluded_slugs={'zzexcluded', 'zzmisspelled'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    (tmp_path / 'ex.json').write_text('{"slugs": ["zzexcluded", "zzmisspelled"]}')
    assert main(['--source', str(src), '--out', str(out), '--exclusions', str(tmp_path / 'ex.json'), '--hosts', HOSTS]) == 1
    assert 'zzmisspelled' in capsys.readouterr().err
    assert not out.exists()


def test_credentialed_feed_that_is_not_excluded_fails_the_snapshot(tmp_path, capsys):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    (tmp_path / 'ex.json').write_text('{"slugs": []}')
    assert main(['--source', str(src), '--out', str(out), '--exclusions', str(tmp_path / 'ex.json'), '--hosts', HOSTS]) == 1
    err = capsys.readouterr().err
    assert 'zzexcluded' in err and 'keep' not in err.split(':', 1)[1]
    assert not out.exists()


def test_exclusions_and_hosts_are_required(tmp_path):
    src = tmp_path / 'live.db'
    make_db(src)
    (tmp_path / 'ex.json').write_text('{"slugs": ["zzexcluded"]}')
    base = ['--source', str(src), '--out', str(tmp_path / 'o.db')]
    assert main(base + ['--hosts', HOSTS]) == 1
    assert main(base + ['--exclusions', str(tmp_path / 'ex.json')]) == 1
    assert main(base + ['--exclusions', str(tmp_path / 'ex.json'), '--hosts', str(tmp_path / 'hosts.txt')]) == 1
