"""Keep overrides, keep partitioning, late keep safety net and learning from kept or cut ads."""

import json
import logging
from ad_detector.boundaries import effective_resolved_action

from ad_validator import restore_uncovered_confirmed_spans, user_trimmed_keep_ranges
from differential_fetcher import differential_region_overlapping

from utils.markers import is_carved
from config import (
    is_pending_review, normalize_segment_category, is_keep_like,
    DEFAULT_SEGMENT_ACTION,
    coerce_bool_setting,
)

from database.settings import registry_get_default

from main_app.verification_reconciliation import _covered_by_cuts
from main_app.pipeline import cuts
from main_app.pipeline.shared import (db, ad_detector)

audio_logger = logging.getLogger('podcast.audio')


def _keep_overridden_by_pattern(ad) -> bool:
    """Standing rule: a defined ad pattern always cuts; keep maps cannot silence it."""
    if ad.get('pattern_defined'):
        ad['keep_overridden_by_pattern'] = True
        return True
    return False


class KeepDifferentialOverride:
    """Second standing rule (#728): audio proven to differ across fetches
    always cuts, whatever the category resolves to.

    A category cannot settle this on its own. `cross_promo` covers both a
    guest plugging their own show, which is why an operator sets keep, and a
    paid dynamically-inserted ad for another podcast. The differential
    separates them: injected audio differs between two fetches of the same
    enclosure and host content does not.
    """

    def __init__(self, dai_differential=None, corr_max: float = 0.0,
                 enabled: bool = False):
        self.dai_differential = dai_differential
        self.corr_max = corr_max
        self.enabled = enabled

    def applies_to(self, ad) -> bool:
        """True when `ad` overlaps a measured differential region; stamps the
        marker with the region that overrode its keep."""
        if not self.enabled:
            return False
        region = differential_region_overlapping(
            self.dai_differential, ad.get('start', 0.0), ad.get('end', 0.0),
            self.corr_max)
        if region is None:
            return False
        ad['keep_overridden_by_differential'] = True
        ad['keep_override_corr'] = region.get('corr')
        return True


def _keep_overridden(ad, differential_override=None) -> bool:
    """Either standing override: a defined pattern, or differential evidence."""
    if _keep_overridden_by_pattern(ad):
        return True
    return bool(differential_override and differential_override.applies_to(ad))


def _clear_hold_for_keep(marker) -> bool:
    """Move a keep marker's hold to hold_cleared_reason: a kept span is never
    force-cut by a stale hold. True when there was a hold to clear."""
    if not marker.get('held_for_review'):
        return False
    marker['hold_cleared_reason'] = marker.get('hold_reason')
    marker['held_for_review'] = False
    marker.pop('hold_reason', None)
    return True


def _load_stored_dai_differential(slug, episode_id):
    """Episode-level differential from episode_details, or None when absent
    or unparseable. The recut path has no in-memory result to reuse."""
    try:
        raw = db.get_episode_dai_differential(slug, episode_id)
        return json.loads(raw) if raw else None
    except (TypeError, ValueError, AttributeError):
        # AttributeError: db is None or the method is absent on an older db.
        return None


def _make_keep_differential_override(dai_differential):
    """Build the differential keep-override from settings, or a disabled one."""
    return KeepDifferentialOverride(
        dai_differential,
        corr_max=db.get_setting_float(
            'differential_measured_corr_max',
            registry_get_default('differential_measured_corr_max')),
        enabled=db.get_setting_bool(
            'dai_differential_overrides_keep',
            default=coerce_bool_setting(
                registry_get_default('dai_differential_overrides_keep'))),
    )


def _partition_keep_ads(all_ads, actions_map, differential_override=None):
    """Split first-pass markers by resolved segment-category action.

    A marker resolving to 'keep' or 'mark' bypasses the validator, reviewer,
    and cut: stamped was_cut=False, action_applied set to the resolved
    action, and pulled out of the list. It also overrides any existing hold,
    since a kept marker can never be force-cut via a stale hold:
    held_for_review is cleared and the original reason kept as
    hold_cleared_reason.
    Exception: a marker from a defined pattern, or one overlapping a
    measured differential region, bypasses keep/mark and lands in the remove
    list stamped with which override caught it.

    Returns (keep_ads, remove_ads); remove_ads is all_ads unchanged when no
    category resolves to keep-like.
    """
    if not any(is_keep_like(action) for action in actions_map.values()):
        return [], all_ads
    keep_ads = []
    remove_ads = []
    for ad in all_ads:
        category = normalize_segment_category(ad.get('category'))
        action = actions_map.get(category)
        if is_keep_like(action):
            if _keep_overridden(ad, differential_override):
                remove_ads.append(ad)
                continue
            ad['was_cut'] = False
            ad['action_applied'] = action
            if _clear_hold_for_keep(ad):
                audio_logger.debug(
                    f"Keep resolution clears hold on marker "
                    f"{ad['start']:.1f}s-{ad['end']:.1f}s "
                    f"(was {ad['hold_cleared_reason']!r})"
                )
            keep_ads.append(ad)
        else:
            remove_ads.append(ad)
    return keep_ads, remove_ads


def _apply_late_keep_safety_net(ads_to_remove, all_ads_with_validation, actions_map,
                                differential_override=None):
    """Backstop right before the pass-1 cut list reaches the audio
    processor: drops any marker whose resolved action is still keep-like.

    A keep-resolving marker synthesized inside _refine_and_validate is
    normally already caught by process_episode's late partition call before
    this runs, so this should find nothing in the ordinary case; it stays
    as a last resort for any marker source that adds one afterward, since
    letting it reach _partition_cut_actions would fall back to
    DEFAULT_SEGMENT_ACTION and still cut it.

    Stamps was_cut=False/action_applied on a caught marker (and its
    all_ads_with_validation master) with the resolved action, clears any
    hold the same way the keep partition does, and removes it from the
    returned cut list.
    Exception: a marker from a defined pattern, or one overlapping a
    measured differential region, stays in the cut list, never kept by
    keep maps.
    Returns ads_to_remove unchanged when no category resolves to keep-like.
    """
    if not any(is_keep_like(action) for action in actions_map.values()):
        return ads_to_remove
    caught, remove = [], []
    for ad in ads_to_remove:
        category = normalize_segment_category(ad.get('category'))
        action = actions_map.get(category)
        if is_keep_like(action):
            caught.append((ad, category, action))
        else:
            remove.append(ad)
    for ad, category, action in caught:
        if _keep_overridden(ad, differential_override):
            remove.append(ad)
        else:
            ad['was_cut'] = False
            ad['action_applied'] = action
            _clear_hold_for_keep(ad)
            master = cuts._find_master(all_ads_with_validation, ad)
            if master is not None:
                master['was_cut'] = False
                master['action_applied'] = action
                _clear_hold_for_keep(master)
            audio_logger.debug(
                f"Late keep safety net: dropping synthesized marker "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s (category={category!r}) "
                f"from the cut list; its resolved action is {action!r}"
            )
    return remove


def _restore_confirmed_spans(ads_to_remove, all_ads_with_validation, corrections,
                             episode_duration, exclude_start_seconds,
                             restore=True, trim_ranges=None):
    """Cut uncovered confirmed spans and keep saved trims out; returns (cuts, trim ranges)."""
    fp_corrections, confirmed = corrections
    if trim_ranges is None:
        trim_ranges = user_trimmed_keep_ranges(confirmed)
    if restore:
        ads_to_remove = restore_uncovered_confirmed_spans(
            ads_to_remove, all_ads_with_validation, confirmed, fp_corrections,
            trim_ranges, episode_duration, exclude_start_seconds)
    ads_to_remove = cuts._carve_cuts_around(
        ads_to_remove, all_ads_with_validation, trim_ranges, user_trim=True)
    return ads_to_remove, trim_ranges


def _partition_cut_actions(ads_to_remove, actions_map):
    """Stamp each cut-list marker with its resolved remove/beep action.

    'keep'/'mark' are already handled by _partition_keep_ads and
    _apply_late_keep_safety_net; this only distinguishes remove from beep.
    A marker with no category key (pass-2 verification ads) normalizes to
    'sponsor'. A keep-like resolution reaching here (unreachable for a
    pass-1 marker) falls back to DEFAULT_SEGMENT_ACTION rather than being
    pulled from the list this late, so the segment still cuts.

    Mutates ad['action_applied'] in place; returns ads_to_remove for chaining.
    """
    for ad in ads_to_remove:
        action = effective_resolved_action(ad, actions_map)
        if action not in ('remove', 'beep'):
            action = DEFAULT_SEGMENT_ACTION
        ad['action_applied'] = action
    return ads_to_remove


def _stamp_and_carve_cuts(slug, episode_id, ads_to_remove, all_ads, segment_actions,
                          keep_ads):
    """Stamp remove/beep on the cuts and their masters, then carve cuts around kept audio."""
    ads_to_remove = _partition_cut_actions(ads_to_remove, segment_actions)
    # Sweeps rebuild dicts, so a cut is not always its master's object.
    for ad in ads_to_remove:
        master = cuts._find_master(all_ads, ad)
        if master is not None:
            master['action_applied'] = ad['action_applied']
    return cuts._carve_cuts_around(ads_to_remove, all_ads, keep_ads, tag=f"[{slug}:{episode_id}]")


def _learn_from_kept_ads(slug, episode_id, keep_ads, segments, audio_path):
    """Learn from keep-action markers that bypassed validation."""
    if not keep_ads or not slug:
        return 0
    patterns_learned = ad_detector.learn_from_detections(
        keep_ads, segments, slug, episode_id, audio_path=audio_path
    )
    if patterns_learned > 0:
        audio_logger.info(
            f"[{slug}:{episode_id}] Learned {patterns_learned} new patterns "
            f"from kept ads"
        )
    return patterns_learned


def _learn_from_applied_cut_ads(slug, episode_id, cut_ads, markers,
                                applied_cuts, original_duration, segments,
                                audio_path):
    """Learn only reviewer-final pass-1 markers present in the rendered cuts."""
    if not slug:
        return 0
    learnable = []
    for ad in cut_ads:
        marker = cuts._find_master(markers, ad)
        # A carved fragment is part of a read, not a whole one.
        if (marker is None or not ad.get('was_cut') or not marker.get('was_cut')
                or is_carved(marker) or is_pending_review(marker)
                or marker.get('action_applied') not in ('remove', 'beep')
                or marker['start'] != ad['start'] or marker['end'] != ad['end']
                or not _covered_by_cuts(marker, applied_cuts, original_duration)):
            continue
        learnable.append(marker)
    if not learnable:
        return 0
    patterns_learned = ad_detector.learn_from_detections(
        learnable, segments, slug, episode_id, audio_path=audio_path)
    if patterns_learned > 0:
        audio_logger.info(
            f"[{slug}:{episode_id}] Learned {patterns_learned} new patterns from cut ads")
    return patterns_learned
