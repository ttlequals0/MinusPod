from scripts.demo.scrub import map_ip, replace_emails, replace_hosts, replace_paths, scrub_source_url, shift_iso, shift_iso_in_text


def test_public_feed_urls_are_kept_and_tokenised_ones_replaced():
    assert scrub_source_url('https://feeds.megaphone.fm/darknetdiaries', 'dd') == 'https://feeds.megaphone.fm/darknetdiaries'
    assert scrub_source_url('https://www.patreon.com/rss/show?auth=abc', 'show') == 'https://example.com/feeds/show.xml'
    assert scrub_source_url('https://host.example/feed/AbCdEfGhIjKlMnOpQrStUv123', 'x') == 'https://example.com/feeds/x.xml'
    assert scrub_source_url('https://host.example/feed.xml?key=1', 'x') == 'https://example.com/feeds/x.xml'


def test_map_ip_keeps_private_and_maps_public_deterministically():
    salt = b'fixed'
    assert map_ip('10.1.2.3', salt) == '10.1.2.3'
    assert map_ip('fe80::1', salt) == 'fe80::1'
    assert map_ip('fd12::9', salt) == 'fd12::9'
    a = map_ip('8.8.8.8', salt)
    assert a == map_ip('8.8.8.8', salt)
    assert a.startswith(('192.0.2.', '198.51.100.', '203.0.113.'))
    assert map_ip('2606:4700::1111', salt).startswith('2001:db8:')
    assert map_ip('not-an-ip', salt) == '203.0.113.0'


def test_shift_iso_variants():
    assert shift_iso('2026-10-01T12:34:56Z', 5) == '2026-10-06T12:34:56Z'
    assert shift_iso('2026-10-01', 5) == '2026-10-06'
    assert shift_iso('2026-10-01 12:34:56', 5) == '2026-10-06 12:34:56'
    assert shift_iso('2026-10-01T00:00:00+00:00', 16) == '2026-10-17T00:00:00+00:00'
    assert shift_iso('2026-10-01T12:34:56.123456', 1) == '2026-10-02T12:34:56.123456'
    assert shift_iso('hello', 5) == 'hello'
    assert shift_iso_in_text('{"a":"2026-10-01T00:00:00Z","b":1}', 1) == '{"a":"2026-10-02T00:00:00Z","b":1}'


def test_replace_hosts_is_case_insensitive_and_bounded():
    out = replace_hosts('see https://Pods.Example.com/x and pods.example.com:8000 but notpods.example.com', ['pods.example.com'])
    assert out == 'see https://demo.minuspod.app/x and demo.minuspod.app:8000 but notpods.example.com'
    assert replace_hosts('https://audio.pods.example.com/f', ['pods.example.com']) == 'https://audio.demo.minuspod.app/f'


def test_invalid_dates_and_userinfo_and_punctuation_hosts():
    assert shift_iso('2026-13-45', 5) == '2026-13-45'
    assert shift_iso_in_text('a 2026-02-30T00:00:00Z', 1) == 'a 2026-02-30T00:00:00Z'
    assert scrub_source_url('https://user:pass@host.example/feed', 'x') == 'https://example.com/feeds/x.xml'
    assert replace_hosts('at pods.example.com, then (pods.example.com).', ['pods.example.com']) == 'at demo.minuspod.app, then (demo.minuspod.app).'
    assert replace_hosts('pods.example.com.evil.net', ['pods.example.com']) == 'pods.example.com.evil.net'


def test_replace_emails_and_paths():
    assert replace_emails('mail me@corp.example now') == 'mail contact@example.com now'
    assert replace_emails('ok@example.org') == 'ok@example.org'
    assert replace_paths('at /app/data/.cache/hub/x and /home/alice/y') == 'at /demo/.cache/hub/x and /demo/y'


def test_replace_paths_respects_context():
    assert replace_paths('https://s.com/home/page') == 'https://s.com/home/page'
    assert replace_paths('/srv-foo and /srv/x') == '/srv-foo and /demo/x'
    assert replace_paths('see /home/alice, ok') == 'see /demo, ok'
