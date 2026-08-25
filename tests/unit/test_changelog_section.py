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


class TestUnwrapLines:
    """Release bodies render single newlines as hard breaks, so the
    changelog's 72-column wrapping must be unwrapped on output."""

    def test_joins_continuation_lines_into_the_bullet(self):
        from changelog_section import unwrap_lines
        wrapped = (
            "### Fixed\n\n"
            "- A long entry that the changelog wraps onto\n"
            "  a second line and then onto\n"
            "  a third line.\n"
        )
        assert unwrap_lines(wrapped) == (
            "### Fixed\n\n"
            "- A long entry that the changelog wraps onto"
            " a second line and then onto a third line.\n"
        )

    def test_headers_blanks_and_new_bullets_stay_separate(self):
        from changelog_section import unwrap_lines
        text = "### Added\n\n- First entry.\n- Second entry.\n"
        assert unwrap_lines(text) == text

    def test_fenced_code_blocks_are_untouched(self):
        from changelog_section import unwrap_lines
        text = (
            "- Entry with a snippet:\n"
            "```\n"
            "  indented code line\n"
            "  another code line\n"
            "```\n"
        )
        assert unwrap_lines(text) == text

    def test_indented_sub_bullets_keep_their_own_lines(self):
        from changelog_section import unwrap_lines
        text = "- Parent entry.\n  - Sub item one.\n  - Sub item two.\n"
        assert unwrap_lines(text) == text
