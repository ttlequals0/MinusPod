"""Unit tests for utils/time.py parse_timestamp function."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.time import parse_timestamp


class TestParseTimestampValid:
    """Tests for valid timestamp inputs that should parse successfully."""

    def test_int_passthrough(self):
        assert parse_timestamp(100) == 100.0

    def test_float_passthrough(self):
        assert parse_timestamp(1178.5) == 1178.5

    def test_zero_int(self):
        assert parse_timestamp(0) == 0.0

    def test_zero_float(self):
        assert parse_timestamp(0.0) == 0.0

    def test_float_string(self):
        assert parse_timestamp("1178.5") == 1178.5

    def test_s_suffix(self):
        assert parse_timestamp("1178.5s") == 1178.5

    def test_hh_mm_ss(self):
        assert parse_timestamp("01:23:45") == 1 * 3600 + 23 * 60 + 45

    def test_hh_mm_ss_ms(self):
        result = parse_timestamp("01:23:45.678")
        assert abs(result - (1 * 3600 + 23 * 60 + 45.678)) < 0.001

    def test_mm_ss(self):
        assert parse_timestamp("23:45") == 23 * 60 + 45

    def test_mm_ss_ms(self):
        result = parse_timestamp("23:45.678")
        assert abs(result - (23 * 60 + 45.678)) < 0.001

    def test_m_ss(self):
        assert parse_timestamp("3:45") == 3 * 60 + 45

    def test_comma_decimal_separator(self):
        result = parse_timestamp("01:23:45,678")
        assert abs(result - (1 * 3600 + 23 * 60 + 45.678)) < 0.001

    def test_whitespace_stripped(self):
        assert parse_timestamp("  1178.5  ") == 1178.5

    def test_integer_string(self):
        assert parse_timestamp("100") == 100.0


class TestParseTimestampInvalid:
    """Tests for invalid inputs that should raise ValueError."""

    def test_none_raises(self):
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            parse_timestamp(None)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            parse_timestamp("")

    def test_non_string_non_numeric_raises(self):
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            parse_timestamp([1, 2, 3])

    def test_garbage_string_raises(self):
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            parse_timestamp("not-a-timestamp")

    def test_dict_raises(self):
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            parse_timestamp({"start": 10})

    def test_bool_raises(self):
        """Booleans are instances of int in Python, so True=1.0, False=0.0."""
        # bool is a subclass of int, so these pass through as numeric
        assert parse_timestamp(True) == 1.0
        assert parse_timestamp(False) == 0.0


class TestAdjustTimestampOverlaps:
    """Overlapping spans in the combined pass-1 + pass-2 cut list must not
    have their shared region subtracted twice."""

    def test_overlapping_spans_subtract_union_once(self):
        from utils.time import adjust_timestamp
        # [100,150] and [140,180] overlap by 10s; union removes 80s.
        ads = [{'start': 100.0, 'end': 150.0}, {'start': 140.0, 'end': 180.0}]
        # Pre-fix: 50 + (40) double-counts the overlap -> 200-90=110.
        assert adjust_timestamp(200.0, ads) == 120.0

    def test_contained_span_ignored(self):
        from utils.time import adjust_timestamp
        ads = [{'start': 100.0, 'end': 200.0}, {'start': 120.0, 'end': 150.0}]
        assert adjust_timestamp(300.0, ads) == 200.0

    def test_touching_spans_merge(self):
        from utils.time import adjust_timestamp
        ads = [{'start': 100.0, 'end': 150.0}, {'start': 150.0, 'end': 180.0}]
        assert adjust_timestamp(200.0, ads) == 120.0

    def test_timestamp_inside_overlap_region_snaps_once(self):
        from utils.time import adjust_timestamp
        ads = [{'start': 100.0, 'end': 150.0}, {'start': 140.0, 'end': 180.0}]
        # 160 is inside the merged [100,180]: snaps to its start.
        assert adjust_timestamp(160.0, ads) == 100.0

    def test_degenerate_span_skipped(self):
        from utils.time import adjust_timestamp
        ads = [{'start': 150.0, 'end': 150.0}, {'start': 100.0, 'end': 120.0}]
        assert adjust_timestamp(200.0, ads) == 180.0

    def test_non_overlapping_unchanged_behavior(self):
        from utils.time import adjust_timestamp
        ads = [{'start': 10.0, 'end': 20.0}, {'start': 50.0, 'end': 60.0}]
        assert adjust_timestamp(100.0, ads) == 80.0
        assert adjust_timestamp(55.0, ads) == 40.0   # snap inside 2nd ad
        assert adjust_timestamp(5.0, ads) == 5.0
