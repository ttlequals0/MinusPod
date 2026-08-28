"""Shared OPML 2.0 builder for feed export.

Used by both the session-gated admin download (`api/feeds.py`) and the
key-gated public import-by-URL route (`main_app/routes.py`), so the two stay
byte-identical. defusedxml has no SubElement/tostring, so stdlib ET is used
for building only (we never parse untrusted OPML here).
"""
import xml.etree.ElementTree as ET


def modified_feed_url(base_url: str, slug: str, key: str | None) -> str:
    """MinusPod-served feed URL, carrying ?key= while feed auth is enabled.

    Single implementation of the keyed feed-URL shape; api.feeds._public_feed_url
    delegates here so the export and the feedUrl the UI shows never drift.
    """
    url = f"{base_url.rstrip('/')}/{slug}"
    return f"{url}?key={key}" if key else url


def build_opml_xml(podcasts: list[dict], mode: str, base_url: str,
                   feed_auth_key: str | None = None) -> str:
    """Render feeds as an OPML 2.0 document string.

    mode='modified' emits MinusPod ad-free feed URLs (keyed when feed_auth_key
    is set); mode='original' emits the upstream source URLs. Callers validate
    mode before calling.
    """
    opml = ET.Element('opml', version='2.0')
    head = ET.SubElement(opml, 'head')
    ET.SubElement(head, 'title').text = 'MinusPod Feeds'
    body = ET.SubElement(opml, 'body')

    for podcast in podcasts:
        title = podcast.get('title') or podcast.get('slug', '')
        if mode == 'modified':
            feed_url = modified_feed_url(base_url, podcast['slug'], feed_auth_key)
        else:
            if podcast.get('feed_type') == 'local':
                continue
            feed_url = podcast.get('source_url', '')
        ET.SubElement(body, 'outline', type='rss', text=title, title=title,
                      xmlUrl=feed_url)

    xml_bytes = ET.tostring(opml, encoding='unicode', xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes
