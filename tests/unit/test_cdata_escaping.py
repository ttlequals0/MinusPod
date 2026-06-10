"""Regression tests for CDATA-terminator escaping in generated RSS.

Upstream <description> content is attacker-influenced (whatever the source
feed publishes). It is emitted inside a ``<![CDATA[...]]>`` block. CDATA has
no character escaping, so a literal ``]]>`` in the source closes the section
early and leaks the remainder as raw markup, corrupting the served feed for
every subscriber of that podcast. ``RSSParser._escape_cdata`` splits the
terminator across two CDATA sections so the literal text is preserved while
the parser never sees a real ``]]>``.
"""
import xml.dom.minidom as minidom

import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


def _cdata_text(xml_fragment: str) -> str:
    """Parse a fragment and return concatenated text of its <description>."""
    doc = minidom.parseString(xml_fragment)
    node = doc.getElementsByTagName("description")[0]
    return "".join(
        c.data
        for c in node.childNodes
        if c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE)
    )


class TestEscapeCdata:
    def test_passthrough_when_no_terminator(self):
        assert RSSParser._escape_cdata("plain text") == "plain text"

    def test_empty_and_none(self):
        assert RSSParser._escape_cdata("") == ""
        assert RSSParser._escape_cdata(None) == ""

    def test_terminator_is_split(self):
        out = RSSParser._escape_cdata("a]]>b")
        assert "]]>" not in out.replace("]]]]><![CDATA[>", "")

    def test_wrapped_output_is_well_formed_and_roundtrips(self):
        evil = 'Buy now ]]><script>alert(1)</script> at example.com'
        fragment = (
            f'<item><description><![CDATA['
            f'{RSSParser._escape_cdata(evil)}'
            f']]></description></item>'
        )
        # Must parse (the unescaped form raises "not well-formed").
        assert _cdata_text(fragment) == evil

    def test_unescaped_form_would_break(self):
        evil = 'x]]>y'
        broken = f'<item><description><![CDATA[{evil}]]></description></item>'
        try:
            minidom.parseString(broken)
            raised = False
        except Exception:
            raised = True
        assert raised, "control: raw ]]> should produce malformed XML"

    def test_multiple_terminators(self):
        evil = ']]>]]>]]>'
        fragment = (
            f'<item><description><![CDATA['
            f'{RSSParser._escape_cdata(evil)}'
            f']]></description></item>'
        )
        assert _cdata_text(fragment) == evil
