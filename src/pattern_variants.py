"""Shared intro/outro variant derivation, union, dedupe, and cap (#399).

One implementation for every path that writes a pattern's intro/outro variants:
manual create, the manual merge endpoint, and auto-promotion. Manual patterns
are born with empty variant arrays, so this derives them from the full
text_template; the merge/promotion paths union arrays from several patterns.
Both keep arrays distinct (phrases >=95% similar on their canonical form are
folded) and bounded (VARIANT_CAP per array) so the budget covers real opening/
closing variation rather than near-duplicates.
"""
import json
from typing import List, Sequence, Tuple

from text_pattern_matcher import _extract_intro_phrase, _extract_outro_phrase
from utils.pattern_similarity import (
    DUPLICATE_THRESHOLD,
    canonicalize_for_dedupe,
    similarity,
)

VARIANT_CAP = 5


def _as_list(value) -> List[str]:
    """Coerce a variants field (list or JSON-string) into a list of strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v.strip()]


def derive_intro_outro(text_template: str) -> Tuple[List[str], List[str]]:
    """Return ([intro], [outro]) phrases derived from a full ad template,
    each empty when extraction yields nothing."""
    if not text_template:
        return [], []
    intro = _extract_intro_phrase(text_template)
    outro = _extract_outro_phrase(text_template)
    return ([intro] if intro else []), ([outro] if outro else [])


def dedupe_and_cap(phrases: Sequence[str], cap: int = VARIANT_CAP) -> List[str]:
    """Keep distinct phrases in order, dropping any >=95% similar (on the
    canonical form) to one already kept, capped at `cap`."""
    kept: List[str] = []
    kept_canon: List[str] = []
    for p in phrases:
        if not isinstance(p, str) or not p.strip():
            continue
        cp = canonicalize_for_dedupe(p)
        if cp and any(similarity(cp, kc) >= DUPLICATE_THRESHOLD for kc in kept_canon):
            continue
        kept.append(p)
        kept_canon.append(cp)
        if len(kept) >= cap:
            break
    return kept


def variants_for_pattern(pattern: dict) -> Tuple[List[str], List[str]]:
    """Intro/outro variants for one pattern: use its stored variants, deriving
    from text_template for whichever side is empty (manual rows have none)."""
    intros = _as_list(pattern.get('intro_variants'))
    outros = _as_list(pattern.get('outro_variants'))
    if not intros or not outros:
        di, do = derive_intro_outro(pattern.get('text_template') or '')
        if not intros:
            intros = di
        if not outros:
            outros = do
    return intros, outros


def merge_variants(patterns: Sequence[dict]) -> Tuple[List[str], List[str]]:
    """Union intro/outro variants across patterns (deriving where empty), then
    dedupe + cap each side. Returns (intro_variants, outro_variants)."""
    all_intros: List[str] = []
    all_outros: List[str] = []
    for p in patterns:
        intros, outros = variants_for_pattern(p)
        all_intros.extend(intros)
        all_outros.extend(outros)
    return dedupe_and_cap(all_intros), dedupe_and_cap(all_outros)
