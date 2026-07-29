"""Truncated feed documents must fail the parse, not yield a short entry list.

A body cut mid-element parses to whatever entries arrived before the cut, so
accepting it silently drops every episode past the truncation point. Only
truncation signatures are rejected: feedparser sets bozo for benign reasons
too, and rejecting all of them would break feeds that work today.
"""

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_truncation_test_')

from rss_parser import RSSParser, _is_truncation_error  # noqa: E402

_parser = RSSParser(base_url='http://localhost:8000')

_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>The Daily Tech Show</title>
<item><title>Episode 1</title><guid>a1b2c3d4e5f6</guid>
<enclosure url="https://example.com/1.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Episode 2</title><guid>b2c3d4e5f6a1</guid>
<enclosure url="https://example.com/2.mp3" type="audio/mpeg" length="1"/></item>
"""

_COMPLETE = _HEAD + "</channel></rss>"
# Cut mid-element, the shape a dropped connection produces.
_TRUNCATED = _HEAD + "<item><title>Episode 3</tit"


def test_complete_feed_parses_with_all_entries():
    feed = _parser.parse_feed(_COMPLETE, source='example-podcast')
    assert feed is not None
    assert len(feed.entries) == 2


def test_truncated_feed_is_rejected():
    assert _parser.parse_feed(_TRUNCATED, source='example-podcast') is None


def test_truncation_markers_recognised():
    assert _is_truncation_error(Exception('mismatched tag: no element found'))
    assert _is_truncation_error(Exception('unclosed token: line 4, column 67'))
    assert _is_truncation_error(Exception('Unexpected end of data'))


def test_benign_bozo_causes_are_not_truncation():
    assert not _is_truncation_error(None)
    assert not _is_truncation_error(Exception('document declared as us-ascii'))
    assert not _is_truncation_error(Exception('undeclared namespace prefix'))


def test_feed_with_benign_bozo_still_parses():
    """An undeclared namespace prefix sets bozo but the document is whole, so
    the entries must survive."""
    body = ("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>The Daily Tech Show</title>
<item><title>Episode 1</title><guid>a1b2c3d4e5f6</guid>
<unknown:tag>x</unknown:tag>
<enclosure url="https://example.com/1.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>""")
    feed = _parser.parse_feed(body, source='example-podcast')
    assert feed is not None
    assert len(feed.entries) == 1
