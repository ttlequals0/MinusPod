"""Pure-logic tests for sweep aggregation math.

No network, no audio decode, no file I/O.
"""
import pytest

from cuebench.sweep import _build_histogram, _build_threshold_table


class TestBuildHistogram:
    def test_empty_scores(self):
        hist = _build_histogram([])
        # All bins present, all zero
        assert all(v == 0 for v in hist.values())
        assert "0.35" in hist
        assert "0.95" in hist

    def test_single_score_in_first_bin(self):
        hist = _build_histogram([0.37])
        assert hist["0.35"] == 1
        assert hist["0.40"] == 0

    def test_score_at_bin_edge_goes_to_lower_bin(self):
        # 0.40 should land in the [0.40, 0.45) bin
        hist = _build_histogram([0.40])
        assert hist["0.40"] == 1
        assert hist["0.35"] == 0

    def test_multiple_scores_distributed(self):
        scores = [0.36, 0.51, 0.52, 0.88]
        hist = _build_histogram(scores)
        assert hist["0.35"] == 1   # 0.36 in [0.35, 0.40)
        assert hist["0.50"] == 2   # 0.51, 0.52 in [0.50, 0.55)
        assert hist["0.85"] == 1   # 0.88 in [0.85, 0.90)
        assert sum(hist.values()) == 4

    def test_scores_below_floor_not_counted(self):
        # Scores below 0.35 should not appear in any bin
        hist = _build_histogram([0.10, 0.20, 0.34])
        assert sum(hist.values()) == 0

    def test_score_at_1_0_counted_in_last_bin(self):
        # np.arange(0.35, 1.05, 0.05) produces a bin starting at 1.00;
        # score 1.0 lands in [1.00, 1.01) and is counted under key "1.00".
        hist = _build_histogram([1.0])
        assert hist.get("1.00", 0) == 1
        assert hist.get("0.95", 0) == 0


class TestBuildThresholdTable:
    def test_empty_scores(self):
        table = _build_threshold_table([])
        assert all(row["matches"] == 0 for row in table)
        thresholds = [row["threshold"] for row in table]
        assert 0.50 in thresholds
        assert 0.95 in thresholds

    def test_counts_correct(self):
        scores = [0.50, 0.60, 0.70, 0.80, 0.90]
        table = _build_threshold_table(scores)
        by_t = {row["threshold"]: row["matches"] for row in table}
        # All 5 scores >= 0.50
        assert by_t[0.50] == 5
        # 0.60, 0.70, 0.80, 0.90 >= 0.60
        assert by_t[0.60] == 4
        # 0.90 >= 0.90
        assert by_t[0.90] == 1
        # No score >= 0.95
        assert by_t[0.95] == 0

    def test_table_length(self):
        # 0.50 to 0.95 in 0.05 steps = 10 rows
        table = _build_threshold_table([])
        assert len(table) == 10

    def test_row_keys(self):
        table = _build_threshold_table([0.7])
        for row in table:
            assert "threshold" in row
            assert "matches" in row

    def test_monotone_decreasing(self):
        scores = [0.51, 0.62, 0.75, 0.83, 0.91]
        table = _build_threshold_table(scores)
        counts = [row["matches"] for row in table]
        for i in range(len(counts) - 1):
            assert counts[i] >= counts[i + 1], (
                f"not monotone at index {i}: {counts[i]} < {counts[i+1]}"
            )
