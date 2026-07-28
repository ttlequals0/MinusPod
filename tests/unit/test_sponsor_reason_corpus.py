"""Sponsor extraction against a corpus of real LLM reason strings.

The model rewords the reason on every run, so patterns keyed to a phrasing
("X sponsor read") only ever cover the sample they were written for. These
strings are real detector output with show identities replaced; they exist so
a change to extraction is measured against variance rather than one example.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='reason_corpus_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest  # noqa: E402

from sponsor_service import SponsorService  # noqa: E402

# (reason, accepted answers). A tuple allows both the prose and the domain
# spelling of a brand, which are equally defensible labels.
CORPUS = [
    ("Host self-promo block: Patreon.com/TheShow, TheShowStore.com, and "
     "HostName.com tour dates call to action", ('Patreon',)),
    ("Orphaned ad-break lead-in 'We'll be right back' immediately before a "
     "splice/digital-silence point where the ad was cut", (None,)),
    ("Back-to-back dynamically inserted ads (audio confirmed DAI): Hykes law "
     "enforcement boots with URL HykesUSA.com, followed by Belmont Park "
     "Village luxury outlet promo; merged (gap <15s)", ('Hykes', 'HykesUSA')),
    ("Dynamically inserted ad block after 'We'll be right back' transition: "
     "Orange County tourism (pickocny.com), Mattress Warehouse Black Friday "
     "in July, and Amazon back-to-school.", ('Orange County',)),
    ("Contiguous host-read sponsor block: PestEase (pesti.com/show, 10% off), "
     "IQ Bar (text keyword to 64000, 20% off), and Jack Archer Jet Setter "
     "Tech Pant (promo code GetJack, 15% off).", ('PestEase',)),
    ("DAI ad block: Orange County/pickocny.com tourism, Mattress Warehouse "
     "Black Friday in July, and Amazon back-to-school spots", ('Orange County',)),
    ("Jack Archer Jet Setter Tech Pant sponsor read with promo code GetJack "
     "and URL JackArcher.com; opens with tail of prior ad",
     ('Jack Archer', 'JackArcher')),
    ("Orphaned URL fragment ('Proofreader.com') isolated between content "
     "gaps, leftover from an incompletely cut ad", ('Proofreader',)),
    ("Full ZipRecruiter sponsor read missed by first pass, includes "
     "ZipRecruiter.com/show URL, free trial call to action", ('ZipRecruiter',)),
    ("Back-to-back host-read sponsor ads for ZipRecruiter "
     "(ZipRecruiter.com/show) and Visible ($25/month, visible.com)",
     ('ZipRecruiter',)),
    ("Back-to-back sponsor reads for LifeLock (LifeLock.com/show, 30% off) "
     "and StreetEasy (dynamically inserted ad), merged due to <15s gap",
     ('LifeLock',)),
    ("Back-to-back host-read sponsor ads for Chime (Chime.com/show) and "
     "Eight Sleep (code at 8sleep.com/show), confirmed by DAI splice",
     ('Chime',)),
    ("ZipRecruiter host-read sponsor segment with URL and call to action",
     ('ZipRecruiter',)),
    ("Host-read sponsor block for Squarespace (Squarespace.com/show, promo "
     "code) followed by Dodge Charger; continues in next window",
     ('Squarespace',)),
    ("Back-to-back host-read sponsor spots: Squarespace (promo code, "
     "Squarespace.com/show) followed by Dodge Charger Scat Pack (Dodge.com)",
     ('Squarespace',)),
    # A later brand having a URL must not outrank the first one named.
    ("Back-to-back host-read sponsor ads: IQ Bar (text keyword to 64000), "
     "PestEase (Pesti.com/show), and Jack Archer (JackArcher.com promo code "
     "GetJack)", ('IQ Bar',)),
    # With no domain to say where the brand ends, the label is capped rather
    # than running on through the product description.
    ("Network-inserted pre-roll ads: LEGO Land Discovery Center Westchester "
     "Ninjago event with 25% discount CTA, followed by Lincoln Tech",
     ('LEGO Land Discovery Center',)),
    ("Lincoln Tech dynamically-inserted sponsor spot (career training, "
     "lincolntech.edu)", ('Lincoln Tech',)),
    # Reasons that name no advertiser must stay empty.
    ("mailing address mentioned in passing", (None,)),
    ("Ad break: Host discusses the news at length", (None,)),
    ("Dynamically inserted pre-roll ads with no named brand at all", (None,)),
    ("Based on the volume anomaly observed in this segment", (None,)),
]


@pytest.mark.parametrize('reason,accepted', CORPUS)
def test_extraction_across_real_phrasings(reason, accepted):
    assert SponsorService.extract_sponsor_from_reason(reason) in accepted


def test_the_corpus_is_not_quietly_shrinking():
    """A regression here is easiest to hide by deleting rows."""
    assert len(CORPUS) >= 22
