"""Reviewer calibration self-test.

Runs a small labeled corpus of ad/non-ad transcripts through the production
`AdReviewer` stack against the configured LLM and reports verdict agreement
plus how often responses carry the structured `is_ad` bool. Every transcript
in the corpus was written for this tool; nothing is sourced from a real feed.

CLI usage (see docs/llm-providers.md):

    PYTHONPATH=src python -m tools.reviewer_calibration

Also auto-runs in a background thread on a reviewer model change, storing its
result under the `reviewer_calibration_last` setting.
"""
from __future__ import annotations

import json
import logging
import sys
import threading

# Defensive sys.path bootstrap so direct `python path/to/script.py` invocation
# works as well as `python -m tools.X`.
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

logger = logging.getLogger(__name__)

# Below this verdict-agreement fraction the CLI exits 1 and the settings
# auto-run hook logs a WARNING instead of INFO.
CALIBRATION_AGREEMENT_THRESHOLD = 0.75

_EPISODE_META = {
    'podcast_name': 'Calibration Corpus',
    'episode_description': '',
    'podcast_description': '',
    'slug': 'reviewer-calibration',
    'podcast_id': 'calibration',
}


def _segments(*lines: tuple) -> list[dict]:
    return [{'start': s, 'end': e, 'text': t} for s, e, t in lines]


# 4 fictional-sponsor ads + 4 non-ads, ~130s segments each; candidate centered so
# the reviewer's +/-60s window pulls the full transcript. Sponsors (Acme Mattress,
# Brightleaf Coffee, Nimbus VPN, Harborline Insurance) are fictional, not from a real feed.
CALIBRATION_CORPUS = [
    {
        'id': 'acme_mattress_ad',
        'expected': 'keep',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': 'Acme Mattress'},
        'segments': _segments(
            (0.0, 35.0,
             "So that's basically the whole debate around morning versus "
             "evening workouts, and I don't think there's a clear winner "
             "honestly. It really depends on your schedule and when your "
             "energy actually peaks during the day."),
            (35.0, 50.0,
             "Alright, before we get into the next segment I want to take "
             "a quick break to hear from today's sponsor."),
            (50.0, 80.0,
             "This episode is brought to you by Acme Mattress. Acme "
             "Mattress builds every mattress with adaptive foam that "
             "contours to your spine and stays cool through the night, so "
             "you fall asleep faster and wake up without the usual aches. "
             "Right now Acme Mattress is offering listeners of this show "
             "forty percent off any mattress plus two free pillows when "
             "you go to acmemattress.com slash pod and use the promo code "
             "POD40 at checkout. That's acmemattress dot com slash pod, "
             "code POD40, for forty percent off your best night's sleep."),
            (80.0, 95.0,
             "Alright, we're back. So let's pick up where we left off on "
             "the sleep science stuff."),
            (95.0, 130.0,
             "One thing that really stuck with me from that study was how "
             "much light exposure in the first hour after waking affects "
             "your circadian rhythm for the rest of the day. It's a "
             "pretty simple habit to change but most people never think "
             "about it."),
        ),
    },
    {
        'id': 'brightleaf_coffee_ad',
        'expected': 'keep',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': 'Brightleaf Coffee'},
        'segments': _segments(
            (0.0, 35.0,
             "We got a listener email this week asking why we haven't "
             "covered the transit expansion story yet, and honestly it's "
             "been sitting in my drafts for a month because there's just "
             "so much to unpack."),
            (35.0, 50.0,
             "But first, a word from the folks who keep this show "
             "running."),
            (50.0, 80.0,
             "Today's episode is sponsored by Brightleaf Coffee. "
             "Brightleaf sources single origin beans directly from small "
             "farms and roasts every batch to order, so what shows up on "
             "your doorstep was picked within the last two weeks, not "
             "sitting in a warehouse for months. Listeners of this show "
             "get twenty five percent off their first bag at "
             "brightleafcoffee.com slash pod with the code LEAF25. Free "
             "shipping on any order over thirty dollars."),
            (80.0, 95.0, "Okay, back to the transit story."),
            (95.0, 130.0,
             "The short version is the city council tabled the vote twice "
             "already, and the latest proposal cuts the northern line "
             "almost entirely, which is going to be a big problem for "
             "anyone commuting from that side of town."),
        ),
    },
    {
        'id': 'nimbus_vpn_ad',
        'expected': 'keep',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': 'Nimbus VPN'},
        'segments': _segments(
            (0.0, 35.0,
             "There's a whole subculture of people who collect vintage "
             "synthesizers just to never actually play them, and I find "
             "that fascinating and a little bit unsettling at the same "
             "time."),
            (35.0, 50.0,
             "Speaking of things worth protecting, let's talk about "
             "today's sponsor."),
            (50.0, 80.0,
             "This segment is brought to you by Nimbus VPN. Nimbus "
             "encrypts your connection on every network you join, keeps "
             "zero logs of your browsing activity, and lets you connect "
             "up to eight devices on a single plan. Head to nimbusvpn.com "
             "slash pod and use code NIMBUS20 to get twenty percent off "
             "any annual plan, plus a thirty day money back guarantee if "
             "it's not for you."),
            (80.0, 95.0,
             "Alright, back to synthesizer collecting, which is a weirdly "
             "deep rabbit hole."),
            (95.0, 130.0,
             "Apparently there's an entire forum dedicated to just "
             "photographing unopened boxes of gear from the eighties, and "
             "some of those threads have thousands of replies."),
        ),
    },
    {
        'id': 'harborline_insurance_ad',
        'expected': 'keep',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': 'Harborline Insurance'},
        'segments': _segments(
            (0.0, 35.0,
             "We've been getting a ton of questions about the home "
             "renovation series, so I think next week we're finally going "
             "to bring on a contractor to answer them directly."),
            (35.0, 50.0, "Before that though, quick break for our sponsor."),
            (50.0, 80.0,
             "This episode is brought to you by Harborline Insurance. "
             "Harborline bundles home and auto coverage into a single "
             "monthly bill and their online quote tool takes about four "
             "minutes to fill out, no phone call required. Get a free "
             "quote at harborlineinsurance.com slash pod, and if you "
             "bundle both policies you'll unlock an additional twelve "
             "percent discount for your first year."),
            (80.0, 95.0, "Alright, back to renovation talk."),
            (95.0, 130.0,
             "The other thing I wanted to mention is that permit costs "
             "have gone up almost across the board this year, so "
             "budgeting an extra buffer for that is probably smart advice "
             "for anyone starting a project soon."),
        ),
    },
    {
        'id': 'guest_book_plug',
        'expected': 'drop',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': ''},
        'segments': _segments(
            (0.0, 35.0,
             "So you've been working in urban planning for almost twenty "
             "years now, and I think a lot of our listeners don't really "
             "understand what that job looks like day to day."),
            (35.0, 50.0,
             "Right, and that's actually a huge part of why I wanted to "
             "write the book, because most people only see the finished "
             "result, not the decade of arguments that got you there."),
            (50.0, 80.0,
             "Yeah, so the book is called The Marginal Hour, it came out "
             "back in March, and it's really about how small procedural "
             "decisions compound over decades into the cities we actually "
             "live in. I wrote most of it during a stretch where I was "
             "between projects, so it's got a lot of the frustration "
             "baked in, honestly. If people want to check it out it's "
             "available wherever books are sold, or they can grab a "
             "signed copy through my personal site, there's a link in the "
             "show notes for that."),
            (80.0, 95.0, "That's a great read, I tore through it in like two days."),
            (95.0, 130.0,
             "One of the chapters that stuck with me most was the one "
             "about parking minimums, which sounds boring but genuinely "
             "changed how I look at every strip mall I drive past now."),
        ),
    },
    {
        'id': 'editorial_brand_discussion',
        'expected': 'drop',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': ''},
        'segments': _segments(
            (0.0, 35.0,
             "Did you catch the story that broke this week about Cascade "
             "Bikes? Apparently there's a class action forming over the "
             "frame welds on last year's commuter line."),
            (35.0, 50.0,
             "Yeah, I saw that. A few owners posted photos of hairline "
             "cracks showing up after less than a year of normal riding, "
             "which is not a good look for a company that built its whole "
             "brand on durability."),
            (50.0, 80.0,
             "From what I can tell, Cascade switched frame suppliers "
             "sometime last spring to cut costs, and it seems like "
             "quality control slipped during that transition. The company "
             "put out a statement saying they're investigating, but they "
             "haven't offered a recall yet, just an extended warranty for "
             "anyone who registers a complaint directly with support."),
            (80.0, 95.0,
             "It's kind of a shame because their earlier lineup had a "
             "really good reputation, this feels like a self-inflicted "
             "problem."),
            (95.0, 130.0,
             "Anyway, let's move on to the segment we actually planned "
             "for today, which is about the new bike lane proposal "
             "downtown."),
        ),
    },
    {
        'id': 'comedic_fake_sponsor',
        'expected': 'drop',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': ''},
        'segments': _segments(
            (0.0, 35.0, "Okay, we're going to do a bit here, so bear with us for a second."),
            (35.0, 50.0,
             "This next part isn't real, just so everyone's clear, we "
             "don't actually have a sponsor like this."),
            (50.0, 80.0,
             "This episode is brought to you by Doomsday Bunker Yogurt, "
             "the yogurt cultured deep underground to survive nuclear "
             "winter and mild social awkwardness alike. Is it dairy? "
             "Nobody knows. Is it legal in all fifty states? Also "
             "unclear. Doomsday Bunker Yogurt: side effects may include "
             "enlightenment, or possibly nothing at all, we are not "
             "doctors and this is not a real product you can purchase "
             "anywhere."),
            (80.0, 95.0, "Okay, bit's over, back to the actual show."),
            (95.0, 130.0,
             "So where were we before I derailed everything with a fake "
             "yogurt company, right, the news story about grocery "
             "delivery drones."),
        ),
    },
    {
        'id': 'topic_transition',
        'expected': 'drop',
        'candidate': {'start': 50.0, 'end': 80.0, 'sponsor': ''},
        'segments': _segments(
            (0.0, 35.0,
             "Alright, that's plenty on the housing market for now, "
             "prices in that segment are wild and we could talk about it "
             "forever."),
            (35.0, 50.0, "Yeah, let's actually shift gears for the back half of the show."),
            (50.0, 80.0,
             "We've got a listener question about home coffee brewing "
             "methods that I want to get into, because there's a lot of "
             "misinformation out there about grind size and water "
             "temperature. Somebody wrote in asking why their pour-over "
             "always tastes bitter no matter what beans they use, and I "
             "think the answer is almost always extraction time, not the "
             "beans themselves."),
            (80.0, 95.0, "Yeah, that's such a common mistake, people blame the beans first."),
            (95.0, 130.0,
             "So let's actually walk through it step by step, starting "
             "with grind consistency, because that's usually where things "
             "go wrong first."),
        ),
    },
]


def _verdict_agrees(verdict: str, expected: str) -> bool:
    if expected == 'keep':
        return verdict in ('confirmed', 'adjust')
    return verdict == 'reject'


def _resolve_calibration_model(db) -> str:
    """review_model wins unless it's the same_as_pass placeholder, in which
    case fall back to the detection pass model. Same rule as
    AdReviewer._resolve_model, which reads the live pass model instead."""
    configured = db.get_setting('review_model')
    if configured and configured != 'same_as_pass':
        return configured
    fallback = db.get_setting('claude_model')
    if fallback:
        return fallback
    raise ValueError(
        'No model configured for reviewer calibration: set review_model '
        'or claude_model first.'
    )


def run_calibration(llm_client=None, model: str | None = None) -> dict:
    """Run CALIBRATION_CORPUS through the production AdReviewer stack.

    Returns per-case verdicts, aggregate agreement, and structured_fraction.
    Raises on a missing model or broken client; the auto-run hook catches this.
    """
    from ad_reviewer import AdReviewer
    from database import Database
    from llm_client import get_llm_client
    from utils.time import utc_now_iso

    db = Database()
    client = llm_client or get_llm_client()
    resolved_model = model or _resolve_calibration_model(db)
    reviewer = AdReviewer(db=db, llm_client=client, sponsor_service=None)

    cases_out = []
    agree_count = 0
    structured_count = 0
    for case in CALIBRATION_CORPUS:
        candidate = case['candidate']
        ad = {'start': candidate['start'], 'end': candidate['end'], 'confidence': 0.9}
        episode_meta = {**_EPISODE_META, 'episode_title': case['id'], 'episode_id': case['id']}
        result = reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=case['segments'], episode_meta=episode_meta,
            pass_num=1, pass_model=resolved_model,
        )
        # A reviewer returning no verdict (empty or unparseable response)
        # counts as a failed case instead of crashing the run.
        verdict = result.verdicts[0] if result.verdicts else None
        verdict_label = verdict.verdict if verdict else 'failure'
        agree = verdict is not None and _verdict_agrees(verdict_label, case['expected'])
        structured = verdict is not None and verdict.structured_is_ad is not None
        if agree:
            agree_count += 1
        if structured:
            structured_count += 1
        cases_out.append({
            'id': case['id'], 'expected': case['expected'],
            'verdict': verdict_label, 'agree': agree, 'structured': structured,
        })

    n = len(CALIBRATION_CORPUS)
    try:
        provider = client.get_provider_name()
    except Exception:
        provider = 'unknown'

    return {
        'model': resolved_model,
        'provider': provider,
        'cases': cases_out,
        'agreement': agree_count / n,
        'structured_fraction': structured_count / n,
        'ran_at': utc_now_iso(),
    }


def maybe_trigger_reviewer_calibration(db, old_value: str | None,
                                       new_value: str | None):
    """Fire run_calibration() in a daemon thread when the reviewer model
    setting actually changed and reviewer_calibration_on_change is enabled.

    Returns the started Thread, or None when not triggered. Never raises or
    blocks: a calibration failure is logged and the settings write proceeds.
    """
    if old_value == new_value:
        return None
    if not db.get_setting_bool('reviewer_calibration_on_change', True):
        return None

    def _run():
        try:
            result = run_calibration()
            db.set_setting(
                'reviewer_calibration_last', json.dumps(result), is_default=False)
            agreement = result.get('agreement', 0.0)
            msg = (f"Reviewer calibration after model change to "
                   f"{new_value!r}: agreement={agreement:.3f}")
            if agreement < CALIBRATION_AGREEMENT_THRESHOLD:
                logger.warning(msg)
            else:
                logger.info(msg)
        except Exception:
            logger.exception("Reviewer calibration self-test failed")

    thread = threading.Thread(target=_run, daemon=True, name="reviewer-calibration")
    thread.start()
    return thread


def _print_table(cases: list[dict]) -> None:
    header = ('case', 'expected', 'verdict', 'agree')
    rows = [(c['id'], c['expected'], c['verdict'], 'yes' if c['agree'] else 'no')
            for c in cases]
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) if rows else len(header[i])
              for i in range(4)]

    def _row(row):
        return ' | '.join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print(_row(header))
    print('-|-'.join('-' * w for w in widths))
    for row in rows:
        print(_row(row))


def main() -> int:
    result = run_calibration()
    _print_table(result['cases'])
    print()
    print(f"model={result['model']} provider={result['provider']} "
          f"agreement={result['agreement']:.3f} "
          f"structured_fraction={result['structured_fraction']:.3f}")
    return 0 if result['agreement'] >= CALIBRATION_AGREEMENT_THRESHOLD else 1


if __name__ == '__main__':
    sys.exit(main())
