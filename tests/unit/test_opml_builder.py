"""build_opml_xml shared helper (2.34.0)."""
import os
import sys

import defusedxml.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.opml import build_opml_xml

BASE = 'https://podfeed.example.com'
KEY = 'a' * 64
PODCASTS = [
    {'slug': 'my-show', 'title': 'My Show', 'source_url': 'https://up.example.com/a.xml'},
    {'slug': 'other', 'title': '', 'source_url': 'https://up.example.com/b.rss'},
]


def _urls(xml):
    return [o.get('xmlUrl') for o in ET.fromstring(xml).findall('.//outline')]


def test_modified_keyed():
    urls = _urls(build_opml_xml(PODCASTS, 'modified', BASE, KEY))
    assert urls == [f'{BASE}/my-show?key={KEY}', f'{BASE}/other?key={KEY}']


def test_modified_keyless_when_no_key():
    urls = _urls(build_opml_xml(PODCASTS, 'modified', BASE, None))
    assert urls == [f'{BASE}/my-show', f'{BASE}/other']


def test_original_uses_source_urls():
    urls = _urls(build_opml_xml(PODCASTS, 'original', BASE, KEY))
    assert urls == ['https://up.example.com/a.xml', 'https://up.example.com/b.rss']


def test_base_url_trailing_slash_stripped():
    urls = _urls(build_opml_xml(PODCASTS[:1], 'modified', BASE + '/', None))
    assert urls == [f'{BASE}/my-show']


def test_structure_and_empty():
    root = ET.fromstring(build_opml_xml([], 'modified', BASE, KEY))
    assert root.tag == 'opml' and root.get('version') == '2.0'
    assert root.find('.//head/title').text == 'MinusPod Feeds'
    assert root.findall('.//outline') == []


def test_title_falls_back_to_slug():
    root = ET.fromstring(build_opml_xml(PODCASTS, 'original', BASE, None))
    assert root.findall('.//outline')[1].get('text') == 'other'


def test_title_override_is_used_in_both_modes():
    podcasts = [{
        'slug': 'my-show',
        'title': 'Source Title',
        'title_override': 'Display Title',
        'source_url': 'https://up.example.com/a.xml',
    }]
    for mode in ('modified', 'original'):
        outline = ET.fromstring(
            build_opml_xml(podcasts, mode, BASE, None)
        ).find('.//outline')
        assert outline.get('text') == 'Display Title'
        assert outline.get('title') == 'Display Title'


def test_blank_title_override_falls_back_to_title():
    podcast = {
        'slug': 'my-show',
        'title': 'Source Title',
        'title_override': '  ',
        'source_url': 'https://up.example.com/a.xml',
    }
    outline = ET.fromstring(
        build_opml_xml([podcast], 'original', BASE, None)
    ).find('.//outline')
    assert outline.get('text') == 'Source Title'
    assert outline.get('title') == 'Source Title'
