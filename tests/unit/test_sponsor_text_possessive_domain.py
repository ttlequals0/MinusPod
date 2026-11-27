import pytest

from sponsor_service import SponsorService


@pytest.mark.parametrize("text", [
    "Black Friday deals are on now at Harlow's. Terms at Harlow's.com slash shipping terms.",
    "Terms at Harlow\u2019s.com slash shipping terms.",
])
def test_possessive_before_tld_is_not_a_fragment(text):
    assert SponsorService.extract_sponsor_from_text(text) == "Harlows"


def test_plain_domain_unchanged():
    assert SponsorService.extract_sponsor_from_text("Visit acme-tools.com/show today") == "Acme Tools"


def test_bare_possessive_fragment_alone_is_not_a_sponsor():
    assert SponsorService.extract_sponsor_from_text("see it's.com now") == "Its"


@pytest.mark.parametrize("text, expected", [
    ("Go to don't.com today", "Dont"),
    ("Go to o'harlow.com today", "Oharlow"),
    ("Go to 'acme.com today", "Acme"),
])
def test_apostrophe_between_letters_is_dropped(text, expected):
    assert SponsorService.extract_sponsor_from_text(text) == expected
