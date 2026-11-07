import sqlite3
from datetime import date
from unittest.mock import patch

import pytest

from scripts.demo.export_allowlist import create_full_schema, SETTINGS_FORCE, SETTINGS_FORCE_UNREGISTERED
from scripts.demo.snapshot import main, snapshot


def make_db(path):
    built = sqlite3.connect(create_full_schema(path.parent / 'built'))
    conn = sqlite3.connect(path)
    built.backup(conn)
    built.close()
    conn.executemany("INSERT INTO podcasts (id, slug, source_url, title, created_at, updated_at) VALUES (?,?,?,?,?,?)", [
        (1, 'keep', 'https://feeds.example.net/keep', 'Keep', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
        (2, 'paid', 'https://www.patreon.com/rss/paid?auth=tok', 'Paid', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
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
    conn.execute("INSERT INTO ad_patterns (id, scope, podcast_id, text_template) VALUES (1, 'podcast', 2, 'paid ad')")
    conn.execute("INSERT INTO audio_fingerprints (pattern_id) VALUES (1)")
    conn.execute("INSERT INTO addressing_log (created_at, podcast_slug, episode_id, pass_name, configured_mode, effective_mode, windows_judged, windows_compliant) VALUES ('2026-08-01T00:00:00Z','keep','old','p1','a','a',1,1)")
    conn.execute("INSERT INTO addressing_log (created_at, podcast_slug, episode_id, pass_name, configured_mode, effective_mode, windows_judged, windows_compliant) VALUES ('2026-10-20T00:00:00Z','keep','new','p1','a','a',1,1)")
    conn.execute("INSERT INTO ad_reviewer_log (episode_id, podcast_id, pass, pool, original_start, original_end, verdict, model_used, success, created_at) VALUES ('old','1',1,'x',0,1,'v','m',1,'2026-10-20 00:00:00')")
    conn.commit()
    conn.close()


def test_snapshot_excludes_windows_scrubs_and_shifts(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    report = snapshot(src, out, days=30, excluded_slugs={'paid'}, hosts=['pods.example.com'], export_day=date(2026, 11, 6), salt=b'salt')
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
    assert conn.execute("SELECT COUNT(*) FROM audio_fingerprints").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ad_patterns").fetchone()[0] == 0
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
        snapshot(src, out, days=30, excluded_slugs=set(), hosts=[], export_day=date(2026, 11, 6), salt=b's')
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'


def test_deleted_rows_do_not_survive_in_file(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    snapshot(src, out, days=30, excluded_slugs={'paid'}, hosts=[], export_day=date(2026, 11, 6), salt=b's')
    assert b'auth=tok' not in out.read_bytes()
    assert b'enc:v1:aaa' not in out.read_bytes()


def test_out_equal_to_source_is_rejected(tmp_path):
    src = tmp_path / 'live.db'
    make_db(src)
    with pytest.raises(ValueError):
        snapshot(src, src, days=30, excluded_slugs=set(), hosts=[], export_day=date(2026, 11, 6), salt=b's')
    assert main(['--source', str(src), '--out', str(src)]) == 1
    assert src.exists()
    assert list(tmp_path.glob('.snapshot-*')) == []


def test_bad_exclusions_json_returns_1(tmp_path):
    src, bad = tmp_path / 'live.db', tmp_path / 'ex.json'
    make_db(src)
    for body in ('[]', '{"slugs": "paid"}', '{"slugs": [1]}'):
        bad.write_text(body)
        assert main(['--source', str(src), '--out', str(tmp_path / 'o.db'), '--exclusions', str(bad)]) == 1


def test_fractional_and_offset_timestamps_shift_in_sql_pass(tmp_path):
    src, out = tmp_path / 'live.db', tmp_path / 'snap.db'
    make_db(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE episodes SET description = '2026-10-01T00:00:00+00:00' WHERE episode_id = 'new'")
    conn.commit()
    conn.close()
    snapshot(src, out, days=30, excluded_slugs=set(), hosts=[], export_day=date(2026, 11, 6), salt=b's')
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT description FROM episodes").fetchone()[0] == '2026-10-17T00:00:00+00:00'
