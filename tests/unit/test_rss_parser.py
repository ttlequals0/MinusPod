"""Tests for RSSParser._get_episode_description fallback logic, plus
the structured xml_forbidden_construct event on XXE rejection."""

import logging

import pytest

import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


class TestGetEpisodeDescription:
    """Verify the description -> subtitle -> content fallback chain."""

    def test_returns_description_when_present(self):
        entry = {"description": "Episode about cats"}
        assert RSSParser._get_episode_description(entry) == "Episode about cats"

    def test_returns_empty_when_all_fields_missing(self):
        entry = {}
        assert RSSParser._get_episode_description(entry) == ""

    def test_returns_empty_when_all_fields_empty(self):
        entry = {"description": "", "subtitle": "", "content": []}
        assert RSSParser._get_episode_description(entry) == ""

    def test_skips_whitespace_only_description(self):
        entry = {"description": "   ", "subtitle": "Real subtitle"}
        assert RSSParser._get_episode_description(entry) == "Real subtitle"

    def test_falls_back_to_subtitle(self):
        """Simulates Relay FM feeds: empty description, content in subtitle."""
        entry = {"description": "", "subtitle": "iTunes subtitle text"}
        assert RSSParser._get_episode_description(entry) == "iTunes subtitle text"

    def test_falls_back_to_content_encoded(self):
        """content:encoded is exposed as a list of dicts by feedparser."""
        entry = {
            "description": "",
            "subtitle": "",
            "content": [{"value": "<p>Rich HTML content</p>"}],
        }
        assert RSSParser._get_episode_description(entry) == "<p>Rich HTML content</p>"

    def test_handles_none_description(self):
        entry = {"description": None, "subtitle": "Fallback"}
        assert RSSParser._get_episode_description(entry) == "Fallback"

    def test_handles_none_subtitle(self):
        entry = {"description": None, "subtitle": None, "content": [{"value": "From content"}]}
        assert RSSParser._get_episode_description(entry) == "From content"

    def test_handles_content_with_none_value(self):
        entry = {"description": "", "content": [{"value": None}]}
        assert RSSParser._get_episode_description(entry) == ""

    def test_handles_content_not_a_list(self):
        entry = {"description": "", "content": "not a list"}
        assert RSSParser._get_episode_description(entry) == ""

    def test_handles_empty_content_list(self):
        entry = {"description": "", "content": []}
        assert RSSParser._get_episode_description(entry) == ""

    def test_description_takes_priority_over_subtitle(self):
        entry = {"description": "Primary", "subtitle": "Secondary"}
        assert RSSParser._get_episode_description(entry) == "Primary"

    def test_subtitle_takes_priority_over_content(self):
        entry = {
            "description": "",
            "subtitle": "iTunes subtitle",
            "content": [{"value": "Content encoded"}],
        }
        assert RSSParser._get_episode_description(entry) == "iTunes subtitle"


class TestXxeStructuredEvent:
    """parse_feed must emit an xml_forbidden_construct log event when
    defusedxml rejects DOCTYPE/ENTITY/EXTERNAL references, rather than
    folding it into the generic `RSS parse warning` stream."""

    def _dtd_payload(self):
        return b"""<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss version="2.0"><channel><title>&xxe;</title></channel></rss>"""

    def test_forbidden_construct_returns_none(self, caplog):
        parser = RSSParser()
        with caplog.at_level(logging.WARNING):
            result = parser.parse_feed(self._dtd_payload())
        assert result is None

    def test_forbidden_construct_event_logged(self, caplog):
        parser = RSSParser()
        with caplog.at_level(logging.WARNING):
            parser.parse_feed(self._dtd_payload())
        # Either the structured `extra` key is present on a record, or
        # the rendered message mentions the forbidden-construct event.
        matches = [
            r for r in caplog.records
            if getattr(r, 'event', None) == 'xml_forbidden_construct'
            or 'xml_forbidden_construct' in r.getMessage()
            or 'forbidden construct' in r.getMessage().lower()
        ]
        assert matches, "expected xml_forbidden_construct event in logs"
