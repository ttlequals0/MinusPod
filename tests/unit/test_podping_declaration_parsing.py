"""Channel-level <podcast:podping> ingestion, per the podping tag spec.

Spec: proposal-docs/podping/podping.md in podcast-namespace. The optional
usesPodping attribute opts a feed out of podping, and each nested
<podcast:hiveAccount account="..."/> names an account allowed to podping
this feed.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='podping_decl_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest

from rss_parser import RSSParser

NS = 'https://podcastindex.org/namespace/1.0'


def _feed(channel_extra: str, ns: str = NS) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="{ns}">
  <channel>
    <title>Test Show</title>
    {channel_extra}
  </channel>
</rss>"""


def _declaration(channel_extra, ns=NS):
    return RSSParser.extract_podping_declaration(_feed(channel_extra, ns))


def test_no_tag_is_unknown():
    d = _declaration('')
    assert d == {'uses_podping': None, 'hive_accounts': []}


def test_bare_tag_means_uses_podping():
    # The spec shows <podcast:podping></podcast:podping> as a valid opt-in.
    assert _declaration('<podcast:podping></podcast:podping>')['uses_podping'] is True


def test_self_closing_tag_means_uses_podping():
    assert _declaration('<podcast:podping/>')['uses_podping'] is True


@pytest.mark.parametrize('value', ['true', 'True', 'TRUE', '1', 'yes'])
def test_uses_podping_true_values(value):
    d = _declaration(f'<podcast:podping usesPodping="{value}"/>')
    assert d['uses_podping'] is True


@pytest.mark.parametrize('value', ['false', 'False', 'FALSE', '0', 'no'])
def test_uses_podping_false_values(value):
    d = _declaration(f'<podcast:podping usesPodping="{value}"/>')
    assert d['uses_podping'] is False


def test_collects_hive_accounts_in_order():
    d = _declaration("""<podcast:podping>
        <podcast:hiveAccount account="podping.aaa"/>
        <podcast:hiveAccount account="podping.bbb"/>
        <podcast:hiveAccount account="podping.ccc"/>
      </podcast:podping>""")
    assert d['uses_podping'] is True
    assert d['hive_accounts'] == ['podping.aaa', 'podping.bbb', 'podping.ccc']


def test_hive_accounts_are_lowercased_and_deduped():
    d = _declaration("""<podcast:podping>
        <podcast:hiveAccount account="Podping.AAA"/>
        <podcast:hiveAccount account="podping.aaa"/>
        <podcast:hiveAccount account="  podping.bbb  "/>
      </podcast:podping>""")
    assert d['hive_accounts'] == ['podping.aaa', 'podping.bbb']


def test_hive_account_without_account_attribute_is_skipped():
    d = _declaration("""<podcast:podping>
        <podcast:hiveAccount/>
        <podcast:hiveAccount account=""/>
        <podcast:hiveAccount account="podping.aaa"/>
      </podcast:podping>""")
    assert d['hive_accounts'] == ['podping.aaa']


def test_hive_accounts_with_opt_out_are_still_reported():
    d = _declaration("""<podcast:podping usesPodping="false">
        <podcast:hiveAccount account="podping.aaa"/>
      </podcast:podping>""")
    assert d['uses_podping'] is False
    assert d['hive_accounts'] == ['podping.aaa']


def test_legacy_namespace_uri_is_accepted():
    d = _declaration(
        '<podcast:podping><podcast:hiveAccount account="podping.aaa"/></podcast:podping>',
        ns='http://podcastindex.org/namespace/1.0')
    assert d['uses_podping'] is True
    assert d['hive_accounts'] == ['podping.aaa']


def test_item_level_podping_is_ignored():
    # Only the channel-level declaration counts.
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="{NS}">
  <channel>
    <title>Test Show</title>
    <item>
      <title>Ep 1</title>
      <podcast:podping><podcast:hiveAccount account="attacker"/></podcast:podping>
    </item>
  </channel>
</rss>"""
    assert RSSParser.extract_podping_declaration(feed) == {
        'uses_podping': None, 'hive_accounts': []}


@pytest.mark.parametrize('payload', ['', None, 'not xml at all', '<rss><channel>'])
def test_malformed_input_is_unknown(payload):
    assert RSSParser.extract_podping_declaration(payload) == {
        'uses_podping': None, 'hive_accounts': []}
