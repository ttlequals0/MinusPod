import random
import sqlite3

from scripts.demo.leak_check import check_files, check_sqlite, check_text, main


def hits(text, hosts=()):
    return sorted(h.pattern for h in check_text(text, 'x', list(hosts)))


def test_detects_secret_shapes_and_paths():
    assert hits('key sk-ant-api03-abcdefghijklmnopqrstuvwxyz') == ['anthropic_key']
    assert hits('key sk-or-v1-abcdefghijklmnopqrstuvwxyz') == ['openrouter_key']
    assert hits('key sk-' + 'proj-abcdefghijklmnopqrstuvwxyz') == ['openai_key']
    assert hits('value enc:v1:abc:def') == ['encrypted_setting']
    assert hits('log at /app/data/logs/x.log') == ['abs_path']
    assert hits('mail me@corp.example') == ['email']
    assert hits('mail me@example.com') == []
    assert hits('https://x.test/feed?key=abcdefghijklmnop1234') == ['feed_key']


def test_public_ip_outside_doc_ranges_only():
    assert hits('from 8.8.8.8') == ['public_ip']
    assert hits('from 203.0.113.9 and 192.0.2.1 and 10.0.0.1') == []
    assert hits('version 2.101.25 shipped') == []
    assert hits('bad 999.1.1.1 and 0.1.2.3') == []
    assert hits('v6 2606:4700::1111') == ['public_ip']
    assert hits('v6 2001:db8::1 and fe80::1') == []
    assert hits('at 12:30:45 today') == []


def test_hosts_are_reported_and_excerpt_is_masked():
    [h] = check_text('see https://pods.internal.example/x', 'f', ['pods.internal.example'])
    assert h.pattern == 'host:pods.internal.example'
    assert 'pods.internal.example' not in h.excerpt
    assert hits('PODS.internal.example.org', ['pods.internal.example']) == []
    assert hits('see Pods.Internal.Example.', ['pods.internal.example']) == ['host:pods.internal.example']


def test_check_files_recurses_and_skips_binaries(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'x.json').write_text('{"k": "enc:v1:zz"}')
    (tmp_path / 'a' / 'x.png').write_bytes(b'enc:v1:zz')
    found = check_files([tmp_path], [])
    assert [h.pattern for h in found] == ['encrypted_setting']
    assert found[0].source.endswith('x.json')


def test_check_sqlite_scans_text_and_blobs(tmp_path):
    db = tmp_path / 'a.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE t (a TEXT, b BLOB)')
    conn.execute('INSERT INTO t VALUES (?, ?)', ('clean', b'from 8.8.8.8'))
    conn.execute('INSERT INTO t VALUES (?, ?)', ('enc:v1:q', None))
    conn.commit()
    conn.close()
    found = check_sqlite(db, [])
    assert sorted((h.source, h.pattern) for h in found) == [
        ('t.a#2', 'encrypted_setting'), ('t.b#1', 'public_ip'),
    ]


def test_cli_exit_codes(tmp_path):
    (tmp_path / 'ok.txt').write_text('nothing here')
    assert main([str(tmp_path)]) == 0
    (tmp_path / 'bad.txt').write_text('enc:v1:zz')
    assert main([str(tmp_path)]) == 2


def test_review_regressions():
    assert hits('mail ME@CORP.EXAMPLE') == ['email']
    assert hits('Chrome/155.0.0.0 Safari/537.36.0.0') == []
    assert hits('::ffff:10.0.0.1') == []
    assert hits('::ffff:8.8.8.8') == ['public_ip']
    assert hits('https://twit.memberful.com/checkout?plan=13428') == []
    assert hits('https://show.supercast.com/private_feed/abc') == ['private_feed_host']
    assert hits('https://www.patreon.com/rss/show?auth=x') == ['private_feed_host']
    assert hits('x\nerr in /app/data/.cache/x: bad') == ['abs_path']
    assert hits('see /opt/app/data/x') == []
    assert hits('xsk-' + 'proj-abcdefghijklmnopqrstuvwxyz') == []
    [h] = check_text('k eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4 z', 'x', [])
    assert 'SflK' not in h.excerpt


def test_blob_scanning_and_cli_errors(tmp_path):
    db = tmp_path / 'a.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE t (b BLOB)')
    conn.execute('INSERT INTO t VALUES (?)', (random.Random(1).randbytes(4096),))
    conn.execute('INSERT INTO t VALUES (?)', (b'note enc:v1:zz',))
    conn.commit()
    conn.close()
    assert [(h.source, h.pattern) for h in check_sqlite(db, [])] == [('t.b#2', 'encrypted_setting')]
    assert main([str(tmp_path / 'missing')]) == 2
    assert main([]) == 2
