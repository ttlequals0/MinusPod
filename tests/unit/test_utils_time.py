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
