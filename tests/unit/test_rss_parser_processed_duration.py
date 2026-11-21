import math

import pytest

from rss_parser import RSSParser


FEED_TEMPLATE = '''<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel><title>Example Feed</title><link>https://example.com</link>
    <description>Example</description>
    <item>
      <title>Example Episode</title><guid>episode-guid</guid>
      <enclosure url="https://example.com/audio.mp3" type="audio/mpeg" />
      {duration}
    </item>
  </channel>
</rss>'''


def _render(duration='', extra_episodes=None):
    parser = RSSParser(base_url='https://minuspod.example')
    return parser.modify_feed(
        FEED_TEMPLATE.format(duration=duration),
        'example-feed',
        extra_episodes=extra_episodes,
    )


def _processed(new_duration):
    parser = RSSParser(base_url='https://minuspod.example')
    episode_id = parser.generate_episode_id('https://example.com/audio.mp3', 'episode-guid')
    return [{'episode_id': episode_id, 'new_duration': new_duration}]


def test_processed_episode_uses_duration_of_served_file():
    output = _render('<itunes:duration>44:01</itunes:duration>', _processed(2304))

    assert '<itunes:duration>2304</itunes:duration>' in output
    assert '<itunes:duration>44:01</itunes:duration>' not in output


def test_processed_episode_gets_duration_when_upstream_omits_it():
    output = _render('', _processed(2304))

    assert '<itunes:duration>2304</itunes:duration>' in output


@pytest.mark.parametrize('duration,seconds', [('1:02:03', 3723), ('1234', 1234), ('12.5', 12)])
def test_unprocessed_episode_emits_upstream_duration_as_seconds(duration, seconds):
    output = _render(f'<itunes:duration>{duration}</itunes:duration>')

    assert f'<itunes:duration>{seconds}</itunes:duration>' in output


@pytest.mark.parametrize('new_duration', [None, 0, math.nan])
def test_invalid_processed_duration_falls_back_to_upstream(new_duration):
    output = _render('<itunes:duration>44:01</itunes:duration>', _processed(new_duration))

    assert '<itunes:duration>2641</itunes:duration>' in output


INJECTION = ('&lt;/itunes:{tag}&gt;&lt;itunes:new-feed-url&gt;https://example.com/x'
             '&lt;/itunes:new-feed-url&gt;')


@pytest.mark.parametrize('tag', ['duration', 'episode'])
def test_itunes_values_cannot_inject_elements(tag):
    from xml.etree import ElementTree

    payload = INJECTION.format(tag=tag)
    output = _render(f'<itunes:{tag}>3600{payload}</itunes:{tag}>')

    assert 'new-feed-url' not in output
    ElementTree.fromstring(output)


@pytest.mark.parametrize('tag,value,expected', [
    ('duration', '3600', '3600'), ('duration', '1:02:03', '3723'), ('duration', '44:01', '2641'),
    ('episode', '12', '12'),
])
def test_valid_itunes_values_are_emitted_normalised(tag, value, expected):
    output = _render(f'<itunes:{tag}>{value}</itunes:{tag}>')

    assert f'<itunes:{tag}>{expected}</itunes:{tag}>' in output


@pytest.mark.parametrize('tag,value', [('episode', '-1'), ('episode', '1.5'), ('duration', 'abc')])
def test_invalid_itunes_values_are_dropped(tag, value):
    output = _render(f'<itunes:{tag}>{value}</itunes:{tag}>')

    assert f'<itunes:{tag}>' not in output
