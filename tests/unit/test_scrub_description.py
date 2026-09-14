"""Tests for prompt text normalization helpers."""
from utils.prompt import scrub_description


def test_scrub_description_strips_html_and_normalizes_content():
    description = (
        "\n<p> Welcome &amp; hello at 1:02. </p>   \n\n"
        "<div>Discussion at 0:45 <br/>continued. </div>\n"
        "<div>More at 01:02:03 and https://example.com/path/to/page</div>\n"
    )

    assert scrub_description(description) == (
        "Welcome & hello at XX:XX.\n"
        "Discussion at XX:XX\ncontinued.\n"
        "More at XX:XX and https://example.com/..."
    )


def test_scrub_description_truncates_at_a_word_boundary():
    assert scrub_description("one two three four", max_length=12) == "one two..."


def test_scrub_description_truncates_long_unbroken_text_at_limit():
    assert scrub_description("abcdefghij", max_length=5) == "abcde..."


def test_scrub_description_edge_cases():
    assert scrub_description("") == ""
    assert scrub_description(None) == ""
    assert scrub_description("foo", max_length=-1) == "..."
    assert scrub_description("foo", max_length=0) == "..."
    assert scrub_description("foo", max_length=1) == "f..."
    assert scrub_description("foo", max_length=400) == "foo"
