"""Tests for utils.feed_guid.compute_feed_guid."""
import uuid

import pytest

from utils.feed_guid import PODCAST_NAMESPACE_GUID, compute_feed_guid


class TestNamespaceConstant:
    def test_namespace_matches_spec(self):
        # Fixed by the Podcast Namespace spec; never change without coordination.
        assert str(PODCAST_NAMESPACE_GUID) == "ead4c236-bf58-58c6-a2c6-a6b28d128cb6"


class TestDeterminism:
    def test_same_url_same_guid(self):
        a = compute_feed_guid("https://mp.example.com/the-daily")
        b = compute_feed_guid("https://mp.example.com/the-daily")
        assert a == b

    def test_output_parses_as_uuid(self):
        result = compute_feed_guid("https://mp.example.com/the-daily")
        # Will raise if not a valid UUID string.
        uuid.UUID(result)

    def test_uuid_is_version_5(self):
        result = compute_feed_guid("https://mp.example.com/the-daily")
        assert uuid.UUID(result).version == 5

    def test_different_urls_yield_different_guids(self):
        a = compute_feed_guid("https://mp.example.com/the-daily")
        b = compute_feed_guid("https://mp.example.com/the-weekly")
        assert a != b


class TestNormalization:
    def test_https_and_http_with_same_host_path_match(self):
        # Scheme is stripped, so both collapse to the same name.
        https_guid = compute_feed_guid("https://mp.example.com/show")
        http_guid = compute_feed_guid("http://mp.example.com/show")
        assert https_guid == http_guid

    def test_trailing_slash_normalized(self):
        with_slash = compute_feed_guid("https://mp.example.com/show/")
        without_slash = compute_feed_guid("https://mp.example.com/show")
        assert with_slash == without_slash

    def test_only_one_trailing_slash_stripped(self):
        # Defensive: a doubled trailing slash should NOT collapse to the
        # single-slash form. This documents the behavior; if someone
        # changes it later they have to update this test consciously.
        single = compute_feed_guid("https://mp.example.com/show")
        double = compute_feed_guid("https://mp.example.com/show//")
        assert single != double

    def test_surrounding_whitespace_stripped(self):
        a = compute_feed_guid("  https://mp.example.com/show  ")
        b = compute_feed_guid("https://mp.example.com/show")
        assert a == b


class TestInvalidInput:
    @pytest.mark.parametrize("bad", [None, "", "   ", "https://", "http://"])
    def test_empty_or_scheme_only_returns_none(self, bad):
        assert compute_feed_guid(bad) is None

    @pytest.mark.parametrize("bad", [123, 4.5, b"https://x/y", ["https://x"], {}])
    def test_non_string_returns_none(self, bad):
        assert compute_feed_guid(bad) is None

    def test_lone_slash_returns_none(self):
        # "https:///" normalizes to "" after scheme+trailing-slash strip.
        assert compute_feed_guid("https:///") is None
