"""Per-sponsor near-duplicate clustering for merge suggestions (#399).

Groups same-sponsor patterns whose canonicalized templates are at least the
variant threshold similar into clusters the UI can offer to fold into one row.
Pairwise SequenceMatcher is O(n*m) per pair, so cluster results are cached per
sponsor group and recomputed only when the group's membership/text signature
changes (covering create, edit, delete, and sponsor reassignment).
"""
import hashlib
import json
from typing import Dict, List, Sequence, Tuple

from utils.pattern_similarity import VARIANT_THRESHOLD, canonicalize_for_dedupe, similarity

# sponsor_id -> (signature, clusters). Per-process cache; a signature mismatch
# always recomputes, so a stale worker can only recompute, never serve wrong
# data.
_CACHE: Dict[object, Tuple[str, List[dict]]] = {}


def _group_signature(patterns: Sequence[dict]) -> str:
    """Stable hash of (id, text_template) for every member. Create, edit,
    delete, and sponsor reassignment all change this, busting the cache."""
    items = sorted((p['id'], p.get('text_template') or '') for p in patterns)
    return hashlib.sha256(json.dumps(items).encode('utf-8')).hexdigest()


def tiebreaker_key(pattern: dict):
    """Keep-target preference: most confirmations, fewest false positives,
    longest template, then lowest id (terminal, stable across runs/machines)."""
    return (
        -(pattern.get('confirmation_count') or 0),
        pattern.get('false_positive_count') or 0,
        -len(pattern.get('text_template') or ''),
        pattern['id'],
    )


def _connected_components(ids: List, edges: List[Tuple]) -> List[List]:
    """Union-find connected components over the similarity edges."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    comps: Dict[object, List] = {}
    for i in ids:
        comps.setdefault(find(i), []).append(i)
    return list(comps.values())


def _cluster_group(patterns: List[dict]) -> List[dict]:
    """Cluster one sponsor group; one suggestion per cluster of >=2 patterns
    connected by >= the variant threshold similarity."""
    by_id = {p['id']: p for p in patterns}
    canon = {p['id']: canonicalize_for_dedupe(p.get('text_template') or '') for p in patterns}
    ids = list(by_id)
    edges: List[Tuple] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if similarity(canon[a], canon[b]) >= VARIANT_THRESHOLD:
                edges.append((a, b))

    suggestions: List[dict] = []
    for comp in _connected_components(ids, edges):
        if len(comp) < 2:
            continue
        members = sorted((by_id[i] for i in comp), key=tiebreaker_key)
        keep = members[0]
        suggestions.append({
            'sponsor_id': keep.get('sponsor_id'),
            'sponsor': keep.get('sponsor'),
            'suggested_keep_id': keep['id'],
            'pattern_ids': [m['id'] for m in members],
            'count': len(members),
        })
    return suggestions


def merge_suggestions(patterns: Sequence[dict]) -> List[dict]:
    """All merge suggestions across sponsor groups, cached per group signature.
    Patterns without a sponsor_id are never clustered (cross-sponsor folding is
    unsafe)."""
    groups: Dict[object, List[dict]] = {}
    for p in patterns:
        sid = p.get('sponsor_id')
        if sid is None:
            continue
        groups.setdefault(sid, []).append(p)

    out: List[dict] = []
    for sid, group in groups.items():
        if len(group) < 2:
            _CACHE.pop(sid, None)
            continue
        sig = _group_signature(group)
        cached = _CACHE.get(sid)
        if cached and cached[0] == sig:
            clusters = cached[1]
        else:
            clusters = _cluster_group(group)
            _CACHE[sid] = (sig, clusters)
        out.extend(clusters)

    # Drop cache entries for sponsors no longer present in this snapshot.
    live = set(groups)
    for sid in [s for s in _CACHE if s not in live]:
        _CACHE.pop(sid, None)
    return out
