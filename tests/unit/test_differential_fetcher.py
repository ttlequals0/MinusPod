"""Unit tests for differential_fetcher UA pool and DAI-likelihood heuristic (Layer 3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import BROWSER_USER_AGENT
from differential_fetcher import (
    REFETCH_USER_AGENTS,
    is_likely_dai_feed,
    pick_refetch_user_agent,
)


def test_pool_has_five_distinct_client_strings():
    assert len(REFETCH_USER_AGENTS) == 5
    assert len(set(REFETCH_USER_AGENTS)) == 5
    joined = ' '.join(REFETCH_USER_AGENTS)
    for client in ('Podcasts/', 'Overcast', 'PocketCasts', 'AntennaPod', 'Castro'):
        assert client in joined


def test_pick_never_returns_first_ua():
    first = REFETCH_USER_AGENTS[0]
    for _ in range(50):
        assert pick_refetch_user_agent(first) != first


def test_pick_returns_pool_member_for_browser_ua():
    assert pick_refetch_user_agent(BROWSER_USER_AGENT) in REFETCH_USER_AGENTS


def test_pick_handles_none_first_ua():
    assert pick_refetch_user_agent(None) in REFETCH_USER_AGENTS


def test_dai_domain_in_direct_host():
    assert is_likely_dai_feed(['https://traffic.megaphone.fm/GLT1234.mp3']) is True


def test_dai_domain_in_prefix_chain_path():
    url = ('https://pdst.fm/e/chrt.fm/track/12345/'
           'traffic.megaphone.fm/EP99.mp3')
    assert is_likely_dai_feed([url]) is True


def test_plain_cdn_is_not_dai():
    assert is_likely_dai_feed(['https://cdn.example.com/ep1.mp3']) is False


def test_empty_and_none_inputs():
    assert is_likely_dai_feed([]) is False
    assert is_likely_dai_feed(None) is False
    assert is_likely_dai_feed([None, '']) is False
