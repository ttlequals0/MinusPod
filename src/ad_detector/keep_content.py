"""Keep-content (whitelist) inversion for ad detection, opt-in per feed.

Normal "blacklist" mode asks the LLM which spans are ads and removes those.
Keep-content mode flips it: the LLM labels the substantive show content and we
remove the complement. It targets feeds with unrecognizable programmatic (DAI)
ads, where host content is easier to identify than the ads.

The dangerous failure is the LLM under-labeling content, which deletes real show
audio. So ``invert_content_to_ads`` is gated: if content coverage looks too low
or the inversion would remove too much, it returns None and the caller falls
back to blacklist detection.
"""


# System prompt for the content-labeling pass. The model marks the substantive
# show content; we keep it and remove the complement. It is inclusive (when
# unsure, label as content): under-labeling deletes real audio, over-labeling
# only leaves an ad in.
CONTENT_SYSTEM_PROMPT = (
    "You label the substantive SHOW CONTENT in a podcast transcript window so a "
    "tool can keep it and remove everything else (ads, sponsor reads, dynamically "
    "inserted promos, trailers).\n\n"
    "CONTENT is the actual program: host discussion, interviews, the episode's "
    "topics, listener questions, banter that is part of the show. NOT content: "
    "advertisements, sponsor messages, 'this episode is brought to you by', promo "
    "codes, cross-promotion of other shows, and programmatic ad breaks.\n\n"
    "Be INCLUSIVE: if a span is ambiguous, label it as content. It is far worse to "
    "drop real show audio than to leave one ad in.\n\n"
    "Return ONLY a JSON array of the content spans in this window, each as "
    '{\"start\": <seconds>, \"end\": <seconds>} using the absolute timestamps shown '
    "in the transcript. No prose, no markdown, just the JSON array. If the entire "
    "window is content, return one span covering it. If the entire window is ads, "
    "return []."
)


def _normalize_content_spans(
    spans: list[dict], total_duration: float, edge_pad: float, min_gap: float,
) -> list[tuple[float, float]]:
    """Grow each content span outward by edge_pad (a speech buffer so the first/last
    syllable is never clipped), clamp to the episode, sort, then merge spans
    closer than min_gap so micro-pauses between sentences are kept."""
    norm: list[tuple[float, float]] = []
    for s in spans:
        try:
            a = max(0.0, float(s['start']) - edge_pad)
            b = min(total_duration, float(s['end']) + edge_pad)
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            norm.append((a, b))
    norm.sort()
    merged: list[tuple[float, float]] = []
    for a, b in norm:
        if merged and a - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def invert_content_to_ads(
    content_spans: list[dict],
    total_duration: float,
    *,
    edge_pad: float,
    min_gap: float,
    min_coverage: float,
    max_removed_fraction: float,
    min_ad_seconds: float,
    max_single_ad_fraction: float,
    max_single_ad_seconds: float,
) -> tuple[list[dict] | None, dict]:
    """Invert content spans to ad spans, gated for safety.

    Returns ``(ads, info)``. ``ads`` is the complement of the content spans, or
    ``None`` if a gate fails (the caller then reverts to blacklist mode). ``info``
    is always populated with the metrics and the failed gate's name
    (``failed_gate`` is ``None`` on success), so rejections can be logged and
    thresholds tuned.

    Gates (in order; the first failure sets ``failed_gate``):
      - ``coverage``: content must cover at least ``min_coverage`` of the episode
      - ``removed_fraction``: inverted cuts may remove at most ``max_removed_fraction``
      - ``single_cut_fraction`` / ``single_cut_seconds``: no single inverted cut
        may exceed ``max_single_ad_fraction`` of the episode or
        ``max_single_ad_seconds``. A giant contiguous cut means a whole content
        window went unlabeled, which the near-complementary coverage/removed
        gates miss; the fraction alone is too loose on multi-hour episodes, so
        the absolute cap backstops it.
    Inverted ad slivers shorter than ``min_ad_seconds`` are dropped.
    """
    info: dict = {
        'merged_content_spans': 0,
        'coverage': 0.0,
        'removed_fraction': 0.0,
        'longest_cut_seconds': 0.0,
        'longest_cut_fraction': 0.0,
        'failed_gate': None,
    }
    if total_duration <= 0:
        info['failed_gate'] = 'zero_duration'
        return None, info
    content = _normalize_content_spans(content_spans, total_duration, edge_pad, min_gap)
    info['merged_content_spans'] = len(content)
    if not content:
        info['failed_gate'] = 'empty_content'
        return None, info

    coverage = sum(b - a for a, b in content) / total_duration

    ads: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in content:
        if a - cursor > 0:
            ads.append((cursor, a))
        cursor = max(cursor, b)
    if total_duration - cursor > 0:
        ads.append((cursor, total_duration))

    removed = sum(b - a for a, b in ads) / total_duration
    longest_cut = max((b - a for a, b in ads), default=0.0)
    longest_cut_fraction = longest_cut / total_duration

    # Record the full picture before gating so a rejection logs every metric,
    # not just the one that tripped.
    info['coverage'] = round(coverage, 4)
    info['removed_fraction'] = round(removed, 4)
    info['longest_cut_seconds'] = round(longest_cut, 1)
    info['longest_cut_fraction'] = round(longest_cut_fraction, 4)

    if coverage < min_coverage:
        info['failed_gate'] = 'coverage'
        return None, info
    if removed > max_removed_fraction:
        info['failed_gate'] = 'removed_fraction'
        return None, info
    # A single huge contiguous cut means a whole content window went unlabeled.
    if longest_cut_fraction > max_single_ad_fraction:
        info['failed_gate'] = 'single_cut_fraction'
        return None, info
    if longest_cut > max_single_ad_seconds:
        info['failed_gate'] = 'single_cut_seconds'
        return None, info

    inverted = [
        {
            'start': round(a, 2),
            'end': round(b, 2),
            'confidence': 0.9,
            'reason': 'keep-content: removed as non-content (inverted)',
            'sponsor': None,
            'detection_stage': 'keep_content',
        }
        for a, b in ads
        if b - a >= min_ad_seconds
    ]
    return inverted, info
