import pytest
from youtube.urls import Source, canonical_source_url, parse, source_url, uploads_playlist, watch_url


@pytest.mark.parametrize('text,expected', [
    ('https://www.youtube.com/playlist?list=PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222', Source('playlist', 'PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222')),
    ('https://youtube.com/watch?v=abc123def45&list=PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222', Source('playlist', 'PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222')),
    ('https://m.youtube.com/playlist?list=UULFabcdefghijklmnopqrstuv', Source('playlist', 'UULFabcdefghijklmnopqrstuv')),
    ('PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222', Source('playlist', 'PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222')),
    ('https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv', Source('channel', 'UCabcdefghijklmnopqrstuv')),
    ('UCabcdefghijklmnopqrstuv', Source('channel', 'UCabcdefghijklmnopqrstuv')),
    ('https://www.youtube.com/@SomeHandle', Source('handle', 'SomeHandle')),
    ('https://www.youtube.com/@SomeHandle/videos', Source('handle', 'SomeHandle')),
    ('@SomeHandle', Source('handle', 'SomeHandle')),
    ('https://www.youtube.com/c/SomeName', Source('custom', 'SomeName')),
    ('https://www.youtube.com/user/SomeUser', Source('user', 'SomeUser')),
    ('https://www.youtube.com/SomeName', Source('custom', 'SomeName')),
    ('https://www.youtube.com/watch?v=abc123def45', Source('video', 'abc123def45')),
    ('https://www.youtube.com/watch?v=abc123def45&t=30s', Source('video', 'abc123def45')),
    ('https://youtu.be/abc123def45?si=x', Source('video', 'abc123def45')),
    ('https://www.youtube.com/live/abc123def45', Source('video', 'abc123def45')),
    ('https://www.youtube.com/shorts/abc-123_def', Source('video', 'abc-123_def')),
])
def test_parse_accepts(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize('text', [
    '', 'https://example.com/feed.xml', 'https://www.youtube.com/watch', 'https://www.youtube.com/watch?v=short',
    'https://youtu.be/short', 'https://youtu.be/', 'https://www.youtube.com/shorts/', 'abc123def45',
    'https://www.youtube.com/feed/subscriptions',
    'https://www.youtube.com/playlist?list=', 'PL<script>', 'https://www.youtube.com/results?search_query=x',
])
def test_parse_rejects(text):
    assert parse(text) is None


def test_helpers():
    assert canonical_source_url('playlist', 'PL1') == 'youtube://playlist/PL1'
    assert canonical_source_url('channel', 'UCx') == 'youtube://channel/UCx'
    assert watch_url('abc') == 'https://www.youtube.com/watch?v=abc'
    assert uploads_playlist('UCabcdefghijklmnopqrstuv', include_shorts=False) == 'UULFabcdefghijklmnopqrstuv'
    assert uploads_playlist('UCabcdefghijklmnopqrstuv', include_shorts=True) == 'UUabcdefghijklmnopqrstuv'
    assert source_url(Source('video', 'abc123def45')) == 'https://www.youtube.com/watch?v=abc123def45'
