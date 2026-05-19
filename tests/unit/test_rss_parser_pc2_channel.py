"""Tests for channel-level Podcasting 2.0 tag emission in RSSParser.

Covers the pass/regenerate/strip contract documented in
``docs/podcasting-2.0.md``: minted guid, locked default, passthrough set,
strip set, txt-purpose filtering, attribute escaping, malformed-input
safety, and end-to-end well-formedness of the served feed.
"""
import os

import defusedxml

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
defusedxml.defuse_stdlib()
from defusedxml.ElementTree import fromstring as _safe_fromstring

import pytest

from rss_parser import RSSParser


PODCAST_NS = "https://podcastindex.org/namespace/1.0"


def _build_parser():
    return RSSParser(base_url="https://mp.example.com")


def _emit(parser: RSSParser, feed_content: str, slug: str = "the-daily") -> str:
    lines = []
    parser._emit_channel_pc2_tags(lines, feed_content, slug)
    return "\n".join(lines)


def _full_feed(parser: RSSParser, upstream_xml: str, slug: str = "the-daily") -> str:
    """Run the full modify_feed pipeline so the emitter output is exercised
    inside the real served feed (root namespace declarations, channel
    metadata, item loop, etc.)."""
    return parser.modify_feed(upstream_xml, slug)


def _minimal_upstream(extra_channel_xml: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="{PODCAST_NS}"
                   xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>The Daily</title>
    <link>https://upstream.example.com/the-daily</link>
    <description>Daily news</description>
    <language>en</language>
    {extra_channel_xml}
    <item>
      <title>Episode 1</title>
      <enclosure url="https://upstream.example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""


class TestMintedGuid:
    def test_emits_minted_guid(self):
        out = _emit(_build_parser(), _minimal_upstream())
        assert "<podcast:guid>" in out

    def test_upstream_guid_not_emitted(self):
        upstream = _minimal_upstream(
            '<podcast:guid>11111111-2222-3333-4444-555555555555</podcast:guid>'
        )
        out = _emit(_build_parser(), upstream)
        assert "11111111-2222-3333-4444-555555555555" not in out

    def test_guid_is_deterministic_for_same_slug(self):
        a = _emit(_build_parser(), _minimal_upstream(), slug="show")
        b = _emit(_build_parser(), _minimal_upstream(), slug="show")
        assert a == b

    def test_guid_differs_per_slug(self):
        a = _emit(_build_parser(), _minimal_upstream(), slug="alpha")
        b = _emit(_build_parser(), _minimal_upstream(), slug="beta")
        assert a != b


class TestLockedDefault:
    def test_locked_defaults_to_yes_when_upstream_silent(self):
        out = _emit(_build_parser(), _minimal_upstream())
        assert "<podcast:locked>yes</podcast:locked>" in out

    def test_locked_passed_through_when_upstream_provides_it(self):
        upstream = _minimal_upstream(
            '<podcast:locked owner="owner@example.com">no</podcast:locked>'
        )
        out = _emit(_build_parser(), upstream)
        assert 'owner="owner@example.com"' in out
        assert ">no</podcast:locked>" in out
        # Default "yes" must NOT also be emitted.
        assert "<podcast:locked>yes</podcast:locked>" not in out


class TestAiContent:
    def test_exactly_one_ai_content_txt_true(self):
        out = _emit(_build_parser(), _minimal_upstream())
        assert out.count('<podcast:txt purpose="ai-content">true</podcast:txt>') == 1

    def test_upstream_ai_content_false_is_overridden(self):
        upstream = _minimal_upstream(
            '<podcast:txt purpose="ai-content">false</podcast:txt>'
        )
        out = _emit(_build_parser(), upstream)
        assert "false" not in out
        assert out.count('<podcast:txt purpose="ai-content">true</podcast:txt>') == 1


class TestStripSet:
    @pytest.mark.parametrize("strip_xml,token", [
        ('<podcast:integrity type="sri" value="sha256-XYZ"/>', "sha256-XYZ"),
        ('<podcast:soundbite startTime="100.0" duration="30.0">Catch</podcast:soundbite>',
         "<podcast:soundbite"),
        ('<podcast:liveItem status="live"/>', "<podcast:liveItem"),
        ('<podcast:alternateEnclosure type="audio/mpeg"/>',
         "<podcast:alternateEnclosure"),
        ('<podcast:source uri="https://orig.example.com/audio.mp3"/>',
         "<podcast:source"),
        ('<podcast:podping/>', "<podcast:podping"),
    ])
    def test_strip_tag_absent_from_output(self, strip_xml, token):
        upstream = _minimal_upstream(strip_xml)
        out = _emit(_build_parser(), upstream)
        assert token not in out


class TestTxtPurposeFiltering:
    def test_verify_token_stripped(self):
        upstream = _minimal_upstream(
            '<podcast:txt purpose="verify">SECRET-VERIFY-TOKEN-9f8a</podcast:txt>'
        )
        out = _emit(_build_parser(), upstream)
        assert "SECRET-VERIFY-TOKEN-9f8a" not in out

    def test_applepodcastsverify_token_stripped(self):
        upstream = _minimal_upstream(
            '<podcast:txt purpose="applepodcastsverify">APPLE-TOKEN-abc123</podcast:txt>'
        )
        out = _emit(_build_parser(), upstream)
        assert "APPLE-TOKEN-abc123" not in out

    def test_generic_txt_passes_through(self):
        upstream = _minimal_upstream(
            '<podcast:txt purpose="custom">freeform metadata</podcast:txt>'
        )
        out = _emit(_build_parser(), upstream)
        assert 'purpose="custom"' in out
        assert "freeform metadata" in out


class TestPassthroughSet:
    def test_funding_passed_through(self):
        upstream = _minimal_upstream(
            '<podcast:funding url="https://example.com/donate">Donate</podcast:funding>'
        )
        out = _emit(_build_parser(), upstream)
        assert '<podcast:funding url="https://example.com/donate">Donate</podcast:funding>' in out

    def test_funding_ampersand_in_url_is_escaped(self):
        upstream = _minimal_upstream(
            '<podcast:funding url="https://example.com/donate?a=1&amp;b=2">Donate</podcast:funding>'
        )
        out = _emit(_build_parser(), upstream)
        assert "a=1&amp;b=2" in out
        # Raw & in attribute is the failure mode we are guarding against.
        assert 'a=1&b=2' not in out

    def test_value_block_with_nested_recipients_preserved(self):
        upstream = _minimal_upstream("""
            <podcast:value type="lightning" method="keysend" suggested="0.00000005">
              <podcast:valueRecipient name="Host" type="node" address="030a58b8" split="50"/>
              <podcast:valueRecipient name="Producer" type="node" address="0288c8" split="50"/>
            </podcast:value>
        """)
        out = _emit(_build_parser(), upstream)
        assert "<podcast:value" in out
        assert out.count("<podcast:valueRecipient") == 2
        assert 'name="Host"' in out
        assert 'name="Producer"' in out

    def test_passthrough_set_full_sweep(self):
        upstream = _minimal_upstream("""
            <podcast:medium>podcast</podcast:medium>
            <podcast:license url="https://example.com/lic">CC-BY-SA</podcast:license>
            <podcast:person role="host">Adam</podcast:person>
            <podcast:updateFrequency complete="false" rrule="FREQ=DAILY">Daily</podcast:updateFrequency>
            <podcast:season name="S1">1</podcast:season>
            <podcast:episode display="1">1</podcast:episode>
            <podcast:trailer pubdate="Mon, 01 Jan 2024 00:00:00 GMT" url="https://example.com/t.mp3" length="1234" type="audio/mpeg">Teaser</podcast:trailer>
            <podcast:socialInteract protocol="activitypub" uri="https://example.com/@show"/>
        """)
        out = _emit(_build_parser(), upstream)
        for needle in [
            "<podcast:medium>podcast</podcast:medium>",
            "<podcast:license",
            "<podcast:person",
            "<podcast:updateFrequency",
            "<podcast:season",
            "<podcast:episode",
            "<podcast:trailer",
            "<podcast:socialInteract",
        ]:
            assert needle in out, f"missing {needle}"

    def test_podcast_block_passed_through_with_all_variants(self):
        # Upstream commonly emits one generic <podcast:block> plus per-platform
        # blocks (apple/spotify/amazon). All four must reach the served feed
        # verbatim. Stripping would silently relist the show in directories
        # the publisher chose to keep it out of.
        upstream = _minimal_upstream("""
            <podcast:block>no</podcast:block>
            <podcast:block id="apple">yes</podcast:block>
            <podcast:block id="spotify">yes</podcast:block>
            <podcast:block id="amazon">yes</podcast:block>
        """)
        out = _emit(_build_parser(), upstream)
        assert out.count("<podcast:block") == 4
        assert '<podcast:block>no</podcast:block>' in out
        assert '<podcast:block id="apple">yes</podcast:block>' in out
        assert '<podcast:block id="spotify">yes</podcast:block>' in out
        assert '<podcast:block id="amazon">yes</podcast:block>' in out

    def test_podcast_complete_passed_through(self):
        upstream = _minimal_upstream('<podcast:complete>yes</podcast:complete>')
        out = _emit(_build_parser(), upstream)
        assert "<podcast:complete>yes</podcast:complete>" in out

    def test_self_closing_block_serializer_does_not_invent_body(self):
        # An upstream that emits <podcast:block id="apple"/> with no body
        # must NOT round-trip as "<podcast:block id='apple'>yes</...>" or
        # any other invented payload. The serializer must keep the empty
        # body shape so consumers see exactly what upstream meant.
        upstream = _minimal_upstream('<podcast:block id="apple"/>')
        out = _emit(_build_parser(), upstream)
        assert '<podcast:block id="apple" />' in out
        assert "<podcast:block id=\"apple\">yes" not in out
        assert "<podcast:block id=\"apple\">no" not in out


class TestMalformedAndEdgeCases:
    def test_empty_feed_content_still_emits_minimum(self):
        out = _emit(_build_parser(), "")
        assert "<podcast:guid>" in out
        assert "<podcast:locked>yes</podcast:locked>" in out
        assert '<podcast:txt purpose="ai-content">true</podcast:txt>' in out

    def test_malformed_xml_does_not_raise(self):
        # Garbage that defusedxml will reject; emitter must swallow.
        broken = "<<not really>>>>>><><><><"
        out = _emit(_build_parser(), broken)
        assert "<podcast:guid>" in out
        assert "<podcast:locked>yes</podcast:locked>" in out
        assert '<podcast:txt purpose="ai-content">true</podcast:txt>' in out

    def test_feed_with_no_podcast_tags_at_all(self):
        upstream = """<?xml version="1.0"?>
        <rss><channel><title>Plain</title></channel></rss>"""
        out = _emit(_build_parser(), upstream)
        assert "<podcast:guid>" in out
        assert "<podcast:locked>yes</podcast:locked>" in out
        assert '<podcast:txt purpose="ai-content">true</podcast:txt>' in out


class TestRegressionGuards:
    def test_only_one_guid_in_output(self):
        # Even when upstream's guid happens to equal our minted one, we must
        # emit exactly one ``<podcast:guid>``: the upstream is always stripped
        # before our minted guid is appended.
        upstream = _minimal_upstream(
            '<podcast:guid>11111111-2222-3333-4444-555555555555</podcast:guid>'
        )
        out = _emit(_build_parser(), upstream)
        assert out.count("<podcast:guid>") == 1

    def test_http_namespace_uri_variant_is_parsed(self):
        # Older feeds use the http:// URI for the podcast namespace. The
        # parser accepts both; passthrough tags must be re-emitted under the
        # canonical https prefix at the root and not duplicated.
        upstream = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="http://podcastindex.org/namespace/1.0">
  <channel>
    <title>Old Feed</title>
    <podcast:funding url="https://example.com/donate">Donate</podcast:funding>
    <podcast:integrity type="sri" value="sha256-MUST-NOT-LEAK-HTTP"/>
  </channel>
</rss>"""
        out = _emit(_build_parser(), upstream)
        assert '<podcast:funding url="https://example.com/donate">Donate</podcast:funding>' in out
        assert "sha256-MUST-NOT-LEAK-HTTP" not in out

    def test_unknown_podcast_localname_at_channel_level_is_dropped(self):
        # The parser only re-emits explicit allow-list entries. Unknown
        # podcast:* localnames (future spec additions, vendor extensions)
        # are skipped to avoid lying-about-cut-audio risk.
        upstream = _minimal_upstream(
            '<podcast:bogusFutureTag attr="x">should not pass through</podcast:bogusFutureTag>'
        )
        out = _emit(_build_parser(), upstream)
        assert "bogusFutureTag" not in out
        assert "should not pass through" not in out

    def test_non_podcast_child_of_value_block_is_dropped(self):
        # Defensive: ``_serialize_podcast_element`` recurses only into podcast:*
        # children, so foreign-namespace children inside a passthrough block
        # are filtered out at re-emit time.
        upstream = _minimal_upstream("""
            <podcast:value type="lightning" method="keysend" suggested="0.0">
              <podcast:valueRecipient name="Host" type="node" address="abc" split="100"/>
              <foreign:custom xmlns:foreign="https://example.com/foreign">DROP_ME</foreign:custom>
            </podcast:value>
        """)
        out = _emit(_build_parser(), upstream)
        assert "<podcast:value" in out
        assert "<podcast:valueRecipient" in out
        assert "DROP_ME" not in out
        assert "foreign:" not in out

    def test_verify_purpose_attribute_also_stripped(self):
        # Hardening: assert the purpose attribute itself is gone, not just
        # the secret body. Catches a regression where the element is emitted
        # with an empty body and the purpose still visible.
        upstream = _minimal_upstream("""
            <podcast:txt purpose="verify">SECRET-VERIFY</podcast:txt>
            <podcast:txt purpose="applepodcastsverify">SECRET-APPLE</podcast:txt>
        """)
        out = _emit(_build_parser(), upstream)
        assert 'purpose="verify"' not in out
        assert 'purpose="applepodcastsverify"' not in out

    def test_self_closing_locked_falls_back_to_default(self):
        # Spec violation: ``<podcast:locked>`` MUST contain "yes" or "no".
        # When upstream emits self-closing locked with only an owner
        # attribute, MinusPod must not propagate the empty body; fall back to
        # the default "yes".
        upstream = _minimal_upstream('<podcast:locked owner="orig@example.com"/>')
        out = _emit(_build_parser(), upstream)
        assert "<podcast:locked>yes</podcast:locked>" in out
        # And no empty self-closing locked leaks into the served feed.
        assert "<podcast:locked " not in out
        assert "<podcast:locked/>" not in out

    def test_locked_with_garbage_text_falls_back_to_default(self):
        # Upstream emits non-spec text inside locked; treat as missing.
        upstream = _minimal_upstream('<podcast:locked>maybe</podcast:locked>')
        out = _emit(_build_parser(), upstream)
        assert "<podcast:locked>yes</podcast:locked>" in out
        assert ">maybe<" not in out

    def test_base_url_with_trailing_slash_yields_same_guid(self):
        # Trailing-slash sensitivity guard: GUID must be stable whether the
        # operator's BASE_URL config has a trailing slash or not.
        without = _emit(RSSParser(base_url="https://mp.example.com"), _minimal_upstream())
        with_slash = _emit(RSSParser(base_url="https://mp.example.com/"), _minimal_upstream())
        # Extract just the guid line from each output.
        def _guid_line(text):
            for line in text.splitlines():
                if line.startswith("<podcast:guid>"):
                    return line
            return ""
        assert _guid_line(without) == _guid_line(with_slash)

    def test_deeply_nested_hostile_feed_does_not_crash(self):
        # Build a pathological deeply nested value block (>16 levels) and
        # confirm the emitter caps depth instead of raising RecursionError.
        nested = "</podcast:value>" * 50
        opening = "<podcast:value>" * 50
        upstream = _minimal_upstream(opening + nested)
        # Just running without raising RecursionError is the assertion.
        out = _emit(_build_parser(), upstream)
        assert "<podcast:guid>" in out
        assert '<podcast:txt purpose="ai-content">true</podcast:txt>' in out


class TestNamespaceEquivalence:
    """The Podcast Namespace spec treats several xmlns URIs as equivalent.

    The reference pc20.xml feed (Podcasting 2.0 show) declares the
    GitHub-blob form, not the canonical podcastindex.org URI. The parser
    MUST accept all spec-equivalent forms or channel passthrough silently
    drops on every real-world feed that uses the alternate URI.
    """

    @pytest.mark.parametrize("xmlns_uri", [
        "https://podcastindex.org/namespace/1.0",
        "http://podcastindex.org/namespace/1.0",
        "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md",
        "http://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md",
        "https://github.com/Podcastindex-org/podcast-namespace/blob/master/docs/1.0.md",
        "http://github.com/Podcastindex-org/podcast-namespace/blob/master/docs/1.0.md",
    ])
    def test_channel_passthrough_works_under_each_equivalent_uri(self, xmlns_uri):
        upstream = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="{xmlns_uri}">
  <channel>
    <title>Variant Feed</title>
    <podcast:funding url="https://example.com/donate">Donate</podcast:funding>
    <podcast:medium>podcast</podcast:medium>
    <podcast:integrity type="sri" value="sha256-MUST-NOT-LEAK"/>
  </channel>
</rss>"""
        out = _emit(_build_parser(), upstream)
        # Passthrough survives under any spec-equivalent URI.
        assert '<podcast:funding url="https://example.com/donate">Donate</podcast:funding>' in out
        assert "<podcast:medium>podcast</podcast:medium>" in out
        # Strip list still applies regardless of which URI declared the namespace.
        assert "sha256-MUST-NOT-LEAK" not in out
        # And the canonical prefix is always used on emission, never the upstream URI.
        assert xmlns_uri not in out

    def test_strip_set_applies_under_github_blob_uri(self):
        # Belt and suspenders for the most likely real-world variant.
        gh_uri = "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md"
        upstream = f"""<?xml version="1.0"?>
<rss xmlns:podcast="{gh_uri}">
  <channel>
    <title>X</title>
    <podcast:guid>SHOULD-BE-REPLACED-BY-MINTED</podcast:guid>
    <podcast:soundbite startTime="100" duration="30">Snip</podcast:soundbite>
    <podcast:txt purpose="verify">VERIFY-TOKEN-GH</podcast:txt>
  </channel>
</rss>"""
        out = _emit(_build_parser(), upstream)
        assert "SHOULD-BE-REPLACED-BY-MINTED" not in out
        assert "<podcast:soundbite" not in out
        assert "VERIFY-TOKEN-GH" not in out


class TestPc20SnapshotFixture:
    """Integration smoke against a trimmed snapshot of feeds.podcastindex.org/pc20.xml.

    pc20.xml is the de-facto reference feed for the Podcast Namespace; it
    declares the GitHub-blob xmlns and exercises podroll with 8
    remoteItems, value with nested recipients, two podcast:person entries,
    funding, medium, and an owner-bearing locked. Holding this as a
    fixture guards against regression in either direction (passthrough
    breakage OR accidental promotion of stripped tags).
    """

    @pytest.fixture
    def pc20_xml(self):
        with open(os.path.join(FIXTURES_DIR, "pc20_snapshot.xml"), "r", encoding="utf-8") as f:
            return f.read()

    def test_channel_passthrough_is_non_empty(self, pc20_xml):
        out = _emit(_build_parser(), pc20_xml)
        # Each of these comes from the real feed and must survive passthrough.
        assert "<podcast:funding" in out
        assert "<podcast:medium>podcast</podcast:medium>" in out
        assert "<podcast:person" in out
        assert "<podcast:podroll>" in out
        assert "<podcast:value " in out
        assert "<podcast:valueRecipient" in out

    def test_locked_carried_with_owner_attribute(self, pc20_xml):
        out = _emit(_build_parser(), pc20_xml)
        assert 'owner="adam@curry.com"' in out
        # Real feed locked body is "yes"; passthrough not default.
        assert '<podcast:locked owner="adam@curry.com">yes</podcast:locked>' in out

    def test_podroll_remoteitems_preserved(self, pc20_xml):
        out = _emit(_build_parser(), pc20_xml)
        # pc20.xml ships exactly 8 remoteItems in its podroll block.
        assert out.count("<podcast:remoteItem") == 8

    def test_minted_guid_replaces_upstream(self, pc20_xml):
        out = _emit(_build_parser(), pc20_xml)
        # pc20.xml's upstream guid value (literal from the feed).
        assert "917393e3-1b1e-5cef-ace4-edaa54e1f810" not in out
        assert out.count("<podcast:guid>") == 1

    def test_ai_content_always_true(self, pc20_xml):
        out = _emit(_build_parser(), pc20_xml)
        assert out.count('<podcast:txt purpose="ai-content">true</podcast:txt>') == 1

    def test_block_and_complete_survive_from_real_fixture(self):
        # The pc20 snapshot itself contains <podcast:complete>no</podcast:complete>
        # and four <podcast:block> entries (generic + apple/spotify/amazon).
        # Before the 2.5.6 passthrough additions these were silently dropped
        # and this fixture-driven test passed regardless.
        with open(os.path.join(FIXTURES_DIR, "pc20_snapshot.xml"), encoding="utf-8") as f:
            served = _full_feed(_build_parser(), f.read())
        # Channel block only; exclude any item-level matches by trimming.
        first_item = served.find("<item>")
        channel = served[:first_item]
        assert channel.count("<podcast:block") == 4
        # Exact value, not just substring presence: the fixture has "no"
        # and that value must survive the round-trip.
        assert "<podcast:complete>no</podcast:complete>" in channel


class TestFullFeedWellFormed:
    def test_served_feed_parses_with_funding_ampersand(self):
        upstream = _minimal_upstream(
            '<podcast:funding url="https://example.com/donate?a=1&amp;b=2">Donate</podcast:funding>'
        )
        served = _full_feed(_build_parser(), upstream)
        # Must parse cleanly. defusedxml raises on malformed XML.
        _safe_fromstring(served.encode("utf-8"))

    def test_served_feed_has_minted_guid_locked_aicontent(self):
        served = _full_feed(_build_parser(), _minimal_upstream())
        root = _safe_fromstring(served.encode("utf-8"))
        ns = {"p": PODCAST_NS}
        channel = root.find("channel")
        assert channel is not None
        assert channel.find("p:guid", ns) is not None
        assert channel.find("p:locked", ns) is not None
        ai_content = channel.findall("p:txt", ns)
        ai_purposes = [t.get("purpose") for t in ai_content]
        assert ai_purposes.count("ai-content") == 1

    def test_served_feed_drops_upstream_integrity_and_verify(self):
        upstream = _minimal_upstream("""
            <podcast:integrity type="sri" value="sha256-MUST-NOT-LEAK"/>
            <podcast:txt purpose="verify">VERIFY-TOKEN-MUST-NOT-LEAK</podcast:txt>
        """)
        served = _full_feed(_build_parser(), upstream)
        assert "sha256-MUST-NOT-LEAK" not in served
        assert "VERIFY-TOKEN-MUST-NOT-LEAK" not in served
