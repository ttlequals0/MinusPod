from utils.http import redact_feed_credentials


GLOBAL_KEY = 'a' * 64
SCOPED_KEY = f"{'b' * 16}.{'c' * 64}"


def test_redacts_global_key_from_path_and_referrer():
    value = f'/show/cover-minuspod-deadbeef-{GLOBAL_KEY}.jpg?next=1'
    assert GLOBAL_KEY not in redact_feed_credentials(value)
    assert redact_feed_credentials(value).endswith('.jpg?next=1')


def test_redacts_scoped_key_without_changing_other_text():
    value = f'https://example.com/show?key={SCOPED_KEY}&page=2'
    redacted = redact_feed_credentials(value)
    assert SCOPED_KEY not in redacted
    assert redacted == 'https://example.com/show?key=[redacted-feed-key]&page=2'
