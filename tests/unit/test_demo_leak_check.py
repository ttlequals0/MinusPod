import random
import json
import sqlite3

import pytest

from scripts.demo.leak_check import check_files, check_sqlite, check_text, main, parse_hosts

H = 'pods.example.com'


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
    assert main([str(tmp_path), '--hosts', H]) == 0
    (tmp_path / 'bad.txt').write_text('enc:v1:zz')
    assert main([str(tmp_path), '--hosts', H]) == 2


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
    assert main([str(tmp_path / 'missing'), '--hosts', H]) == 2
    assert main(['--hosts', H]) == 2


def test_ip_in_url_path_but_not_browser_tokens():
    assert hits('https://ipinfo.io/8.8.8.8/json') == ['public_ip']
    assert hits('ip/8.8.8.8') == ['public_ip']
    assert hits('Chrome/155.0.0.0') == []
    assert hits('Mobile Safari/537.36.0.0') == []


def test_strict_patterns_run_on_binary_blobs(tmp_path):
    db = tmp_path / 'a.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE t (b BLOB)')
    conn.execute('INSERT INTO t VALUES (?)', (b'enc:v1:zz' + bytes(240),))
    conn.execute('INSERT INTO t VALUES (?)', (random.Random(2).randbytes(4096) + b' 8.8.8.8',))
    conn.commit()
    conn.close()
    assert [(h.source, h.pattern) for h in check_sqlite(db, [])] == [('t.b#1', 'encrypted_setting')]


def test_token_query_params():
    assert hits('https://x.test/a.mp3?token=abc123') == ['token_param']
    assert hits('https://x.test/a.mp3?id=1&token-hash=abc&token-time=99') == ['token_param', 'token_param']
    assert hits('the token was refreshed') == []


def test_parse_hosts_rejects_empty_and_non_hostnames():
    assert parse_hosts(' a.example.com, b.example.org ,') == ['a.example.com', 'b.example.org']
    for bad in ('', None, ' , ', '/home/me/hosts.txt', 'localhost', 'a b.example.com', 'a.example.com,hosts/x.txt'):
        with pytest.raises(ValueError):
            parse_hosts(bad)
    with pytest.raises(ValueError, match=r"'x'"):
        parse_hosts('hosts.txt,x')


def test_cli_requires_hosts_and_rejects_bad_ones(tmp_path):
    (tmp_path / 'ok.txt').write_text('nothing here')
    assert main([str(tmp_path)]) == 2
    assert main([str(tmp_path), '--hosts', '/tmp/hosts.txt']) == 2


def test_excluded_slugs_hit_contents_and_paths(tmp_path):
    ex = tmp_path / 'ex.json'
    ex.write_text(json.dumps({'slugs': ['zz-paid']}))
    scan = tmp_path / 'scan'
    (scan / 'artwork' / 'feeds').mkdir(parents=True)
    (scan / 'artwork' / 'feeds' / 'zz-paid.jpg').write_bytes(b'\xff\xd8')
    (scan / 'artwork' / 'feeds' / 'other.jpg').write_bytes(b'\xff\xd8')
    args = [str(scan), '--hosts', H, '--exclusions', str(ex)]
    assert main([str(scan), '--hosts', H]) == 0
    assert main(args) == 2
    (scan / 'artwork' / 'feeds' / 'zz-paid.jpg').unlink()
    assert main(args) == 0
    (scan / 'zz-paid').mkdir()
    assert main(args) == 2
    (scan / 'zz-paid').rmdir()
    (scan / 'index.json').write_text('{"/feeds/ZZ-PAID": 1}')
    assert main(args) == 2
    (scan / 'index.json').write_text('{"/feeds/zz-paid-two": 1}')
    assert main(args) == 0


def test_check_files_reports_slug_path_hit(tmp_path):
    (tmp_path / 'ep').mkdir()
    (tmp_path / 'ep' / 'zz-paid.jpg').write_bytes(b'x')
    [h] = check_files([tmp_path], [], ['zz-paid'])
    assert h.pattern == 'slug:zz-paid' and h.source.endswith('zz-paid.jpg')


def test_key_prefixes_need_a_word_boundary():
    for slug in ('full-disk-abcdefghijklmnopqrstuvwxyz', 'ta' + 'sk-' + 'ant-abcdefghijklmnopqrstuvwxyz',
                 'de' + 'sk-' + 'or-abcdefghijklmnopqrstuvwxyz'):
        assert hits('https://x.test/' + slug) == [], slug
    assert hits('key sk-' + 'abcdefghijklmnopqrstuvwxyz0123') == ['openai_key']
    assert hits('(sk-' + 'ant-api03-abcdefghijklmnopqrstuvwxyz)') == ['anthropic_key']


def _host_hit_dir(tmp_path):
    (tmp_path / 'a.txt').write_text('see https://two.internal.example/x tail')
    return str(tmp_path)


def test_redact_hides_hosts_and_excerpts(tmp_path, capsys):
    assert main([_host_hit_dir(tmp_path), '--hosts', 'one.internal.example,two.internal.example', '--redact']) == 2
    out = capsys.readouterr().out
    assert 'host:#2 | 1' in out
    assert 'internal.example' not in out
    assert 'see https' not in out
    assert 'offset' in out


def test_github_actions_redacts_without_the_flag(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    main([_host_hit_dir(tmp_path), '--hosts', 'one.internal.example,two.internal.example'])
    out = capsys.readouterr().out
    assert 'host:#2' in out
    assert 'internal.example' not in out


def test_plain_run_still_prints_the_excerpt(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    main([_host_hit_dir(tmp_path), '--hosts', 'one.internal.example,two.internal.example'])
    out = capsys.readouterr().out
    assert 'host:two.internal.example' in out
    assert 'see https' in out


def test_jwt_needs_a_boundary():
    tok = 'ey' + 'Jabcdefghijk.abcdefghijkl.abcdef'
    assert hits('x' + tok) == []
    assert hits('t ' + tok) == ['jwt']


def _slug_scan(tmp_path):
    ex = tmp_path / 'ex.json'
    ex.write_text(json.dumps({'slugs': ['zz-paid']}))
    scan = tmp_path / 'scan'
    scan.mkdir()
    (scan / 'zz-paid.txt').write_text('see https://two.internal.example/x')
    return scan, ex


def test_redact_with_slugs_withholds_every_location(tmp_path, capsys):
    scan, ex = _slug_scan(tmp_path)
    assert main([str(scan), '--hosts', 'two.internal.example', '--exclusions', str(ex), '--redact']) == 2
    out = capsys.readouterr().out
    assert 'zz-paid' not in out
    assert 'internal.example' not in out
    assert '(location withheld) | offset' in out


def test_redacted_slug_hit_is_named_by_index(tmp_path, capsys):
    scan, ex = _slug_scan(tmp_path)
    (scan / 'zz-paid.txt').write_text('clean')
    assert main([str(scan), '--hosts', H, '--exclusions', str(ex), '--redact']) == 2
    out = capsys.readouterr().out
    assert 'slug:#1 | 1' in out
    assert 'zz-paid' not in out
    assert '(location withheld)' in out


def test_redact_without_slugs_keeps_the_path(tmp_path, capsys):
    (tmp_path / 'fixture.json').write_text('see https://two.internal.example/x')
    main([str(tmp_path), '--hosts', 'two.internal.example', '--redact'])
    assert 'fixture.json | offset' in capsys.readouterr().out


def test_invalid_host_entry_is_not_echoed_under_redaction(tmp_path, capsys):
    (tmp_path / 'ok.txt').write_text('x')
    assert main([str(tmp_path), '--hosts', 'a.example.com,secretname', '--redact']) == 2
    err = capsys.readouterr().err
    assert 'invalid host entry #2' in err
    assert 'secretname' not in err
    with pytest.raises(ValueError, match='invalid host entry #1'):
        parse_hosts('x', redact=True)
