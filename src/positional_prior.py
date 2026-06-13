"""Learned per-podcast ad-break positional prior (issue #360).

Learns where ad breaks historically start in a feed from stored cut history
and user corrections. The resulting prior feeds two consumers:
- a scrutiny hint appended to the first-pass LLM prompt, and
- per-feed position boosts in AdValidator (replacing the global zones).
"""
import json
import logging
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import (
    POSITIONAL_PRIOR_MIN_EPISODES, POSITIONAL_PRIOR_RECENT_EPISODES,
    POSITIONAL_PRIOR_MIN_EPISODE_SECONDS, POSITIONAL_PRIOR_MIN_ZONE_SUPPORT,
    POSITIONAL_PRIOR_CLUSTER_GAP, POSITIONAL_PRIOR_MAX_ZONE_SPAN,
    POSITIONAL_PRIOR_ZONE_MARGIN, POSITIONAL_PRIOR_MAX_ZONES,
    POSITIONAL_PRIOR_EVENT_DEDUPE_GAP, POSITIONAL_PRIOR_MIN_LLM_CONFIDENCE,
    POSITIONAL_PRIOR_MIN_BOOST, POSITIONAL_PRIOR_MAX_BOOST,
    POSITIONAL_PRIOR_MAX_DURATION_RATIO, POSITIONAL_PRIOR_HISTOGRAM_BUCKETS,
    PATTERN_CORRECTION_OVERLAP_THRESHOLD,
)
from utils.time import format_duration, overlap_ratio, ranges_overlap

logger = logging.getLogger(__name__)

# Stages whose evidence is independent of position and the LLM. Everything
# else (claude, verification, the legacy 'first_pass' stamp, missing stages,
# heuristic rolls) is subject to the confidence floor: a cut that only
# cleared the threshold because of a position boost must never reinforce
# the prior, and unknown future stages default to the safe side.
TRUSTED_STAGES = ('fingerprint', 'text_pattern', 'language', 'manual', 'vad_gap')


@dataclass
class LearnedZone:
    center: float            # median normalized cut start (0.0-1.0)
    low: float               # zone lower bound, normalized
    high: float              # zone upper bound, normalized
    support: int             # distinct episodes contributing
    boost: float             # confidence boost for cuts starting in this zone


@dataclass
class AdDistribution:
    """Where a feed's ad cuts land across episodes, for the UI panel.

    Setting-independent and always returned (never None) so the panel can
    render an empty state.
    """
    episodes_considered: int
    median_duration: float
    bucket_count: int
    buckets: List[int]       # cut-start counts per normalized-position bin
    total_events: int
    zones: List[LearnedZone]  # learned prior zones (empty below the gate)


@dataclass
class PositionalPrior:
    episodes_considered: int
    median_duration: float   # median original_duration of learning episodes
    zones: List[LearnedZone]

    def applies_to(self, episode_duration: float) -> bool:
        """Whether this prior is usable for an episode of the given length.

        Zones are normalized fractions; an episode much shorter or longer
        than the feed median would map them onto unrelated content.
        """
        if episode_duration <= 0:
            return False
        ratio = POSITIONAL_PRIOR_MAX_DURATION_RATIO
        return (self.median_duration / ratio
                <= episode_duration
                <= self.median_duration * ratio)


def _episode_event_positions(markers: List[Dict], corrections: List[Dict],
                             duration: float) -> List[float]:
    """Collect normalized ad-break start positions for one episode."""
    false_positives = [c for c in corrections
                       if c['correction_type'] == 'false_positive']
    adjustments = [c for c in corrections
                   if c['correction_type'] == 'boundary_adjustment']

    starts = []
    for marker in markers:
        if not marker.get('was_cut'):
            continue
        start = marker.get('start')
        end = marker.get('end')
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        stage = marker.get('detection_stage')
        confidence = marker.get('confidence') or 0.0
        if stage not in TRUSTED_STAGES and confidence < POSITIONAL_PRIOR_MIN_LLM_CONFIDENCE:
            continue
        if any(overlap_ratio(c['start'], c['end'], start, end)
               >= PATTERN_CORRECTION_OVERLAP_THRESHOLD for c in false_positives):
            continue
        for adj in adjustments:
            # Match by the marker bounds the user adjusted (orig_*); the
            # corrected bounds may have moved anywhere.
            if ranges_overlap(adj.get('orig_start', adj['start']),
                              adj.get('orig_end', adj['end']), start, end):
                start = adj['start']
                break
        starts.append(start)

    for correction in corrections:
        if correction['correction_type'] in ('create', 'confirm'):
            starts.append(correction['start'])

    positions = sorted(min(max(s / duration, 0.0), 1.0) for s in starts)
    deduped = []
    for pos in positions:
        # Events this close within one episode count once, e.g. a confirm
        # correction duplicating its detected marker.
        if not deduped or pos - deduped[-1] > POSITIONAL_PRIOR_EVENT_DEDUPE_GAP:
            deduped.append(pos)
    return deduped


def _collect_events(episodes: List[Dict],
                    corrections: Optional[List[Dict]]) -> tuple:
    """Reduce episode history to (durations, events).

    events is a list of (episode_id, normalized_position) for every learnable
    ad-break start; durations is one entry per considered episode.
    """
    corrections_by_episode: Dict[str, List[Dict]] = {}
    for correction in corrections or []:
        corrections_by_episode.setdefault(correction['episode_id'], []).append(correction)

    durations = []
    events = []  # (episode_id, normalized position)
    for episode in episodes:
        duration = episode.get('original_duration')
        if not duration or duration <= 0:
            continue
        markers = episode.get('ad_markers') or []
        if markers and not any('was_cut' in m for m in markers):
            # Marker set predates confidence gating (e.g. the retry-ad-detection
            # endpoint overwrites ad_markers_json with raw detection output);
            # neither evidence nor a zero-ad episode -- leave it out entirely.
            continue
        durations.append(duration)
        episode_corrections = corrections_by_episode.get(episode['episode_id'], [])
        for pos in _episode_event_positions(markers, episode_corrections, duration):
            events.append((episode['episode_id'], pos))

    return durations, events


def _build_zones(considered: int, events: List[tuple]) -> List[LearnedZone]:
    """Cluster pooled normalized positions into supported zones (empty if none).

    The episode-count gate lives here so every consumer (the prior and the
    distribution panel) agrees on when zones are eligible: a feed with few
    episodes never produces zones even if their cuts happen to align.
    """
    if considered < POSITIONAL_PRIOR_MIN_EPISODES:
        return []

    # 1D gap-merge clustering over pooled normalized positions. The span cap
    # keeps slowly drifting break positions from chaining into one giant zone.
    events = sorted(events, key=lambda e: e[1])
    clusters: List[List[tuple]] = []
    for event in events:
        if (clusters
                and event[1] - clusters[-1][-1][1] <= POSITIONAL_PRIOR_CLUSTER_GAP
                and event[1] - clusters[-1][0][1] <= POSITIONAL_PRIOR_MAX_ZONE_SPAN):
            clusters[-1].append(event)
        else:
            clusters.append([event])

    zones = []
    for cluster in clusters:
        support = len({episode_id for episode_id, _ in cluster})
        support_fraction = support / considered
        if support_fraction < POSITIONAL_PRIOR_MIN_ZONE_SUPPORT:
            continue
        positions = [pos for _, pos in cluster]
        # Scales linearly from MIN_BOOST at the support gate to MAX_BOOST at 100%
        boost = POSITIONAL_PRIOR_MIN_BOOST + (
            (POSITIONAL_PRIOR_MAX_BOOST - POSITIONAL_PRIOR_MIN_BOOST)
            * (support_fraction - POSITIONAL_PRIOR_MIN_ZONE_SUPPORT)
            / (1.0 - POSITIONAL_PRIOR_MIN_ZONE_SUPPORT))
        zones.append(LearnedZone(
            center=statistics.median(positions),
            low=max(0.0, min(positions) - POSITIONAL_PRIOR_ZONE_MARGIN),
            high=min(1.0, max(positions) + POSITIONAL_PRIOR_ZONE_MARGIN),
            support=support,
            boost=boost,
        ))

    zones.sort(key=lambda z: z.support, reverse=True)
    return sorted(zones[:POSITIONAL_PRIOR_MAX_ZONES], key=lambda z: z.center)


def build_prior(slug: str, episodes: List[Dict],
                corrections: Optional[List[Dict]] = None) -> Optional[PositionalPrior]:
    """Build a positional prior from episode cut history.

    Args:
        episodes: dicts with episode_id, original_duration, ad_markers (parsed list)
        corrections: dicts with episode_id, correction_type, start, end
            (boundary_adjustment also carries orig_start/orig_end)

    Returns:
        PositionalPrior, or None when history is insufficient (caller falls
        back to the global position zones).
    """
    durations, events = _collect_events(episodes, corrections)
    considered = len(durations)
    if considered < POSITIONAL_PRIOR_MIN_EPISODES:
        logger.info(f"[{slug}] Positional prior declined: insufficient history "
                    f"({considered} < {POSITIONAL_PRIOR_MIN_EPISODES} episodes)")
        return None

    zones = _build_zones(considered, events)
    if not zones:
        logger.info(f"[{slug}] Positional prior declined: no zone reached "
                    f"{POSITIONAL_PRIOR_MIN_ZONE_SUPPORT:.0%} support "
                    f"across {considered} episodes")
        return None

    zone_summary = ', '.join(
        f"{z.center:.1%} (support {z.support}/{considered}, boost {z.boost:.3f})"
        for z in zones)
    logger.info(f"[{slug}] Positional prior: {considered} episodes considered, "
                f"{len(zones)} zones: {zone_summary}")
    return PositionalPrior(episodes_considered=considered,
                           median_duration=statistics.median(durations),
                           zones=zones)


def build_distribution(slug: str, episodes: List[Dict],
                       corrections: Optional[List[Dict]] = None) -> AdDistribution:
    """Build the always-available ad-position distribution from cut history.

    Unlike build_prior this never returns None: the histogram is shown even
    for feeds with little history; zones are filled only once the prior gate
    is met (5+ episodes, 60% support).
    """
    durations, events = _collect_events(episodes, corrections)
    considered = len(durations)

    buckets = [0] * POSITIONAL_PRIOR_HISTOGRAM_BUCKETS
    for _, pos in events:
        idx = min(int(pos * POSITIONAL_PRIOR_HISTOGRAM_BUCKETS),
                  POSITIONAL_PRIOR_HISTOGRAM_BUCKETS - 1)
        buckets[idx] += 1

    zones = _build_zones(considered, events)

    return AdDistribution(
        episodes_considered=considered,
        median_duration=statistics.median(durations) if durations else 0.0,
        bucket_count=POSITIONAL_PRIOR_HISTOGRAM_BUCKETS,
        buckets=buckets,
        total_events=len(events),
        zones=zones,
    )


def _load_history(db, slug: str, exclude_episode_id: Optional[str] = None) -> tuple:
    """Load a feed's recent cut history from the database as (episodes, corrections)."""
    rows = db.get_recent_episode_ad_history(
        slug, exclude_episode_id=exclude_episode_id,
        limit=POSITIONAL_PRIOR_RECENT_EPISODES,
        min_duration=POSITIONAL_PRIOR_MIN_EPISODE_SECONDS)

    episodes = []
    for row in rows:
        try:
            markers = json.loads(row['ad_markers_json'])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[{slug}:{row['episode_id']}] Skipping episode with "
                           f"unparseable ad_markers_json in positional prior")
            continue
        if not isinstance(markers, list):
            continue
        episodes.append({
            'episode_id': row['episode_id'],
            'original_duration': row['original_duration'],
            'ad_markers': markers,
        })

    corrections = db.get_podcast_corrections_for_prior(
        slug, [e['episode_id'] for e in episodes])
    return episodes, corrections


def compute_positional_prior(db, slug: str,
                             exclude_episode_id: Optional[str] = None
                             ) -> Optional[PositionalPrior]:
    """Load a feed's cut history from the database and build its prior."""
    episodes, corrections = _load_history(db, slug, exclude_episode_id)
    return build_prior(slug, episodes, corrections)


def compute_ad_distribution(db, slug: str) -> AdDistribution:
    """Load a feed's cut history and build its ad-position distribution.

    Setting-independent (no experiment-toggle gate): purely informational for
    the UI panel.
    """
    episodes, corrections = _load_history(db, slug)
    return build_distribution(slug, episodes, corrections)


def load_positional_prior(db, slug: str, episode_id: str,
                          episode_duration: float) -> Optional[PositionalPrior]:
    """Resolve the prior for one episode run: setting gate, compute, length gate.

    Shared by the processing pipeline and the retry-ad-detection endpoint.
    Never raises: prior failure must not fail detection.
    """
    try:
        if not db.get_setting_bool('positional_prior_enabled', default=False):
            return None
        prior = compute_positional_prior(db, slug, exclude_episode_id=episode_id)
    except Exception as e:
        logger.warning(f"[{slug}:{episode_id}] Positional prior computation failed: {e}")
        return None
    if prior is None:
        return None
    if not prior.applies_to(episode_duration):
        logger.info(
            f"[{slug}:{episode_id}] Skipping positional prior: episode duration "
            f"{episode_duration:.0f}s vs feed median {prior.median_duration:.0f}s")
        return None
    return prior


def format_prior_hint(prior: Optional[PositionalPrior],
                      total_duration_seconds: float) -> str:
    """Render the LLM prompt hint for this episode's duration.

    Returns an empty string when there is nothing to say; phrased as
    attention guidance so the hint can never assert an ad into existence.
    """
    if prior is None or not prior.zones or total_duration_seconds <= 0:
        return ""
    times = ', '.join(format_duration(zone.center * total_duration_seconds)
                      for zone in prior.zones)
    return (
        f"Historical ad-break positions for this show (learned from "
        f"{prior.episodes_considered} previous episodes): ad breaks have "
        f"typically started near {times}.\n"
        f"Scrutinize the transcript around those times especially carefully "
        f"for ad transitions. This is attention guidance only: do NOT report "
        f"an ad at those times unless the transcript content there is "
        f"actually advertising, and do NOT ignore ads found elsewhere.\n"
    )
