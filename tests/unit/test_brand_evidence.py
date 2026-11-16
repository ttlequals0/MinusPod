"""has_brand_evidence: the text ties a name to a brand by domain or promo code."""
import pytest

from sponsor_service import has_brand_evidence


@pytest.mark.parametrize('name,text', [
    ('Wise', 'Send money abroad at wise.com today.'),
    ('Wise', 'Go to trywise.com/show for a free transfer.'),
    ('Wise', 'Head to https://www.wise.com/podcast now.'),
    ('Wise', 'Visit wise dot com slash show.'),
    ('Wise', 'Visit WISE DOT COM today.'),
    ('Wise', 'Visit wise dot org for details.'),
    ('Wise', 'Visit wise.net for details.'),
    ('Wise', 'Visit wise .com for details.'),
    ('Wise', 'Use code WISE at checkout.'),
    ('Wise', 'Use promo code wise20 for ten percent off.'),
    ('Acme Tools', 'Go to acmetools.com today.'),
    ('Acme Tools', 'Use code acmetools for a discount.'),
])
def test_text_ties_the_name_to_a_brand(name, text):
    assert has_brand_evidence(name, text) is True


@pytest.mark.parametrize('name,text', [
    ('Day', 'Free 365 day returns, and 365 day returns on everything.'),
    ('Wise', 'The wise women in your life. Wise words from the wise.'),
    ('Wise', 'Visit acme.com for details.'),
    ('Wise', 'Use code ACME at checkout.'),
    ('Wise', 'Wise is great. We love wise.'),
    ('Wise', 'It was a wise day. Com is short for company.'),
    ('Wise', ''),
    ('', 'Visit wise.com'),
])
def test_nothing_else_counts(name, text):
    assert has_brand_evidence(name, text) is False
