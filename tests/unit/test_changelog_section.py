import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from changelog_section import extract_section  # noqa: E402

SAMPLE = """# Changelog

Intro text.

## [2.72.0] - 2026-07-22

### Added

- Feature A.

## [2.71.0] - 2026-07-22

### Fixed

- Fix B.
"""


def test_extracts_middle_section_stops_at_next_header():
    out = extract_section(SAMPLE, "2.72.0")
    assert "Feature A" in out
    assert "2.71.0" not in out
    assert "## [2.72.0]" not in out


def test_extracts_last_section_runs_to_end():
    assert extract_section(SAMPLE, "2.71.0") == "### Fixed\n\n- Fix B.\n"


def test_missing_version_raises_keyerror():
    with pytest.raises(KeyError):
        extract_section(SAMPLE, "9.9.9")


ROLLUP_SAMPLE = """# Changelog

## [2.76.1] - 2026-07-23

### Changed

- Panel tweak.

## [2.76.0] - 2026-07-23

### Added

- Big batch.

## [2.75.0] - 2026-07-22

### Fixed

- Old fix.
"""


def test_rollup_includes_all_sections_since_previous_release():
    from changelog_section import extract_rollup
    out = extract_rollup(ROLLUP_SAMPLE, "2.76.1", "2.75.0")
    assert "## 2.76.1" in out and "Panel tweak" in out
    assert "## 2.76.0" in out and "Big batch" in out
    assert "Old fix" not in out


def test_rollup_single_section_keeps_plain_body():
    from changelog_section import extract_rollup
    out = extract_rollup(ROLLUP_SAMPLE, "2.76.1", "2.76.0")
    assert out == "### Changed\n\n- Panel tweak.\n"


def test_rollup_target_missing_raises_keyerror():
    from changelog_section import extract_rollup
    with pytest.raises(KeyError):
        extract_rollup(ROLLUP_SAMPLE, "9.9.9", "2.75.0")
