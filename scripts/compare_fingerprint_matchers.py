#!/usr/bin/env python3
"""Compare the old audio fingerprint matcher with the current one on a copy of real data.

Usage:
    PYTHONPATH=src python scripts/compare_fingerprint_matchers.py \
        --db /path/to/copy-of/podcast.db --audio-root /path/to/copy-of/podcasts --out ./report-dir

The old matcher is read from git at run time (`git show <old-ref>:src/audio_fingerprinter.py`)
into a private module. The database is opened read-only and nothing touches the network.
Exit code: 0 gate passed, 2 gate failed, 1 error. The report holds instance identifiers
unless --redact is given.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / 'src'))

import audio_fingerprinter as af  # noqa: E402
from utils.markers import parse_ad_markers  # noqa: E402
from utils.pattern_scope import pattern_passes_scope  # noqa: E402

DEFAULT_OLD_REF = 'v2.101.34'
OLD_MODULE_NAME = '_fingerprint_matcher_old'
GATE_SCORE = 0.70
BAND_LOW = 0.65
CLOSE_EVIDENCE_LIMIT_S = 3.0
EDGE_TOLERANCE_S = 1.0
LOW_SIMILARITY = 0.70
LOW_COVERAGE = 0.6
NEAR_BAND = (0.65, 0.72)
FAR_FROM_MARKER_S = 30.0
SHORT_PATTERN_S = 10.0
LONG_PATTERN_S = 30.0
AUDIO_SUFFIXES = {'.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.flac', '.mp4'}
LIVE_DATA_DIRS = (REPO_ROOT / 'data', Path('/app/data'))
WAL_FRESH_S = 60
SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
SETTING_KEYS = ('fingerprint_match_strictness', 'fingerprint_min_coverage',
                'fingerprint_gap_tolerance_s', 'fingerprint_min_ad_length_s')

GATE_TEXT = (
    "Release gate: the release does not go ahead if the new matcher loses a match the old "
    "matcher made in scope with an old score of 0.70 or more (the old matcher's own "
    "similarity over min(10 s, stored length) at its start), unless explain_match names the "
    "rejecting gate and the reason is accepted, or if a kept remainder of a clipped match lies "
    "outside that match's own span. A lost match that another pattern's new match covers by more "
    "than half is listed as superseded and does not block. Old matches scoring 0.65 to 0.70 are "
    "listed with explain_match output and a verdict column left for the maintainer, and do not block.")


def load_old_matcher(ref=DEFAULT_OLD_REF, *, repo=REPO_ROOT, source_text=None):
    """Import the old audio_fingerprinter.py blob of a git ref as a private module."""
    if source_text is None:
        source_text = subprocess.run(
            ['git', '-C', str(repo), 'show', f'{ref}:src/audio_fingerprinter.py'],
            capture_output=True, text=True, check=True, timeout=60).stdout
    with tempfile.TemporaryDirectory(prefix='fp-old-matcher-') as tmp:
        path = Path(tmp) / 'audio_fingerprinter_old.py'
        path.write_text(source_text, encoding='utf-8')
        spec = importlib.util.spec_from_file_location(OLD_MODULE_NAME, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[OLD_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(OLD_MODULE_NAME, None)
            raise
    return module


class Redactor:
    """Stable short hashes for slugs, episode ids and sponsors when enabled."""

    def __init__(self, enabled):
        self.enabled = enabled

    def _h(self, kind, value):
        if not self.enabled or value is None:
            return value
        return f"{kind}-{hashlib.sha256(f'{kind}:{value}'.encode()).hexdigest()[:8]}"

    def slug(self, v):
        return self._h('podcast', v)

    def episode(self, v):
        return self._h('episode', v)

    def sponsor(self, v):
        return self._h('sponsor', v)


def open_readonly(path):
    """SQLite connection that cannot write."""
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only = ON')
    return conn


def check_db_path(db_path):
    """Refuse the live data directory; warn on a fresh WAL file. Returns warnings."""
    resolved = Path(db_path).resolve()
    for live in LIVE_DATA_DIRS:
        if live.resolve() in resolved.parents:
            raise SystemExit(f"refusing to read {resolved}: it sits in a live data directory; use a copy")
    warnings = []
    wal = resolved.with_name(resolved.name + '-wal')
    if wal.exists() and time.time() - wal.stat().st_mtime < WAL_FRESH_S:
        warnings.append(f"{wal.name} was written in the last {WAL_FRESH_S} s; "
                        "the database may be open in another process")
    return warnings


def check_out_dir(out):
    """Refuse an output directory inside the repo tree."""
    resolved = Path(out).resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit("refusing to write the report inside the repo tree; pass --out elsewhere")


class _SettingsShim:
    """Just enough of Database for clamped_fingerprint_setting."""

    def __init__(self, values):
        self.values = values

    def get_setting_float(self, key, default):
        try:
            return float(self.values[key])
        except (KeyError, TypeError, ValueError):
            return default


def load_settings(conn):
    raw = {r['key']: r['value'] for r in conn.execute(
        f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(SETTING_KEYS))})",  # noqa: S608
        SETTING_KEYS)}
    shim = _SettingsShim(raw)
    clamped = {k: af.clamped_fingerprint_setting(shim, k) for k in SETTING_KEYS}
    try:
        stored = float(raw['fingerprint_match_strictness'])
    except (KeyError, ValueError):
        stored = None
    lo = af.FINGERPRINT_SETTING_RANGES['fingerprint_match_strictness'][1]
    return clamped, {'stored': stored, 'used': clamped['fingerprint_match_strictness'],
                     'clamped_up': stored is not None and stored < lo - 1e-9}


def load_patterns(conn, decode):
    """Active enabled fingerprints with scope fields, decoded; same filter as the product."""
    rows = conn.execute(
        """SELECT af.pattern_id, af.fingerprint, af.duration, ks.name AS sponsor,
                  COALESCE(ks.segment_category, ap.category) AS category,
                  ap.scope, ap.network_id, ap.podcast_id, ap.source, ap.sponsor_id
           FROM audio_fingerprints af
           JOIN ad_patterns ap ON af.pattern_id = ap.id
           LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
           WHERE ap.is_active = 1 AND af.enabled = 1""").fetchall()
    patterns = []
    for r in rows:
        data = r['fingerprint']
        ints = decode(data.decode('utf-8', 'ignore') if isinstance(data, bytes) else str(data))
        patterns.append({**dict(r), 'ints': ints})
    return patterns


def load_sponsor_tags(conn):
    out = {}
    for r in conn.execute("SELECT id, tags FROM known_sponsors WHERE is_active = 1"):
        try:
            tags = json.loads(r['tags'] or '[]')
        except (TypeError, ValueError):
            tags = []
        out[r['id']] = set(tags) if isinstance(tags, list) else set()
    return out


def find_original(audio_root, slug, episode_id):
    """Retained original of an episode under audio_root, or None."""
    if not (SAFE_NAME.match(str(slug)) and SAFE_NAME.match(str(episode_id))):
        return None
    root = Path(audio_root).resolve()
    for base in (root / slug / 'episodes', root / 'podcasts' / slug / 'episodes'):
        if base.is_dir():
            for cand in sorted(base.glob(f'{episode_id}-original.*')):
                if cand.suffix.lower() in AUDIO_SUFFIXES and cand.is_file():
                    return cand
    return None


def load_episodes(conn, podcast=None):
    sql = """SELECT e.id AS row_id, e.episode_id, p.slug,
                    COALESCE(NULLIF(TRIM(p.network_id_override), ''), p.network_id) AS network,
                    p.tags AS podcast_tags, ed.ad_markers_json
             FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
             LEFT JOIN episode_details ed ON ed.episode_id = e.id"""
    args = ()
    if podcast:
        sql += " WHERE p.slug = ?"
        args = (podcast,)
    return conn.execute(sql + " ORDER BY e.id", args).fetchall()


class FpcalcProvider:
    """Episode fingerprints through the product's fpcalc path, cached by path, mtime and size."""

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.fingerprinter = af.AudioFingerprinter(db=None)

    def cache_file(self, path):
        st = Path(path).stat()
        key = hashlib.sha1(f'{path}|{st.st_mtime_ns}|{st.st_size}'.encode()).hexdigest()
        return self.cache_dir / f'{key}.json'

    def __call__(self, path):
        cache = self.cache_file(path)
        if cache.is_file():
            d = json.loads(cache.read_text())
            return d['ints'], d['fp_duration'], d['total_duration']
        total = self.fingerprinter._get_audio_duration(str(path))
        if total <= 0:
            return None
        full = self.fingerprinter._generate_full_fingerprint(
            str(path), timeout=self.fingerprinter._full_fingerprint_timeout(total))
        if full is None:
            return None
        ints, fp_duration = full
        cache.write_text(json.dumps({'ints': ints, 'fp_duration': fp_duration,
                                     'total_duration': total}))
        return ints, fp_duration, total


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def _bucket(seconds):
    if seconds < SHORT_PATTERN_S:
        return 'under_10s'
    return '10_to_30s' if seconds <= LONG_PATTERN_S else 'over_30s'


def _markers(raw):
    out = []
    for m in parse_ad_markers(raw) or []:
        try:
            out.append((float(m['start']), float(m['end'])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _best_marker(markers, start, end):
    best = max(markers, key=lambda m: _overlap(m[0], m[1], start, end), default=None)
    if best and _overlap(best[0], best[1], start, end) > 0.5 * (end - start):
        return best
    return None


def _marker_distance(markers, start, end):
    """Seconds from [start, end] to the nearest marker, 0 when one overlaps, None without markers."""
    return min((max(0.0, m[0] - end, start - m[1]) for m in markers), default=None)


def _edge_row(kind, ref, start, end):
    """Edge offsets of [start, end] against a reference span; positive means outside."""
    out_start, out_end = ref[0] - start, end - ref[1]
    return {'ref': kind, 'ref_start': ref[0], 'ref_end': ref[1], 'start_outside_s': out_start,
            'end_outside_s': out_end,
            'overshoot': out_start > EDGE_TOLERANCE_S or out_end > EDGE_TOLERANCE_S,
            'undershoot': -out_start > EDGE_TOLERANCE_S or -out_end > EDGE_TOLERANCE_S}


def _explain(ep_ints, fp_duration, pattern, settings):
    try:
        return af.explain_match(
            ep_ints, fp_duration, pattern,
            strictness=settings['fingerprint_match_strictness'],
            min_coverage=settings['fingerprint_min_coverage'],
            gap_tolerance_s=settings['fingerprint_gap_tolerance_s'])
    except Exception as e:  # keep the run going; the row says why
        return {'error': f'{type(e).__name__}: {e}', 'matched': False, 'rejected_by': None}


def classify_loss(old, ex, superseded):
    """('superseded', why), ('explained', gate) or ('unexplained', reason) for a lost old match."""
    if superseded:
        return 'superseded', "another pattern's new match covers more than half of it"
    if ex is None or 'error' in ex:
        return 'unexplained', 'explain_match unavailable'
    if ex.get('matched') or ex.get('rejected_by') is None:
        return 'unexplained', 'explain_match accepts it but the scan returned none'
    if 'start' in ex and _overlap(ex['start'], ex['end'], old.start, old.end) <= 0:
        return 'unexplained', 'explain_match measured a different place'
    return 'explained', ex['rejected_by']


def compare_episode(ctx, ep, ep_ints, fp_duration, total_duration, old_matcher):
    """Run both matchers on one episode and return the per-episode result."""
    R, settings = ctx['redact'], ctx['settings']
    all_patterns, scope_ids = ctx['patterns'], ep['scope_ids']
    in_scope = [p for p in all_patterns if p['pattern_id'] in scope_ids]
    by_id = {p['pattern_id']: p for p in all_patterns}

    t0 = time.perf_counter()
    old_matches = old_matcher._find_matches_fast(
        ep_ints, fp_duration, [(p['pattern_id'], p['ints'], p['duration'], p['sponsor'], p['category'])
                               for p in all_patterns if p['ints'] is not None],
        total_duration, 600, None) if all_patterns else []
    old_s = time.perf_counter() - t0
    new_tuples = [(p['pattern_id'], p['ints'], p['duration'], p['sponsor'], p['category'])
                  for p in in_scope if p['ints'] is not None]
    t0 = time.perf_counter()
    new_matches = af.match_fingerprints(
        ep_ints, fp_duration, new_tuples,
        strictness=settings['fingerprint_match_strictness'],
        min_coverage=settings['fingerprint_min_coverage'],
        gap_tolerance_s=settings['fingerprint_gap_tolerance_s'],
        min_ad_length_s=settings['fingerprint_min_ad_length_s']) if new_tuples else []
    new_s = time.perf_counter() - t0

    ident = {'podcast': R.slug(ep['slug']), 'episode': R.episode(ep['episode_id'])}
    markers = ep['markers']
    res = {'old_s': old_s, 'new_s': new_s, 'old_count': len(old_matches), 'new_count': len(new_matches),
           'lost_to_scope': [], 'lost': [], 'pairs': 0, 'split': [], 'gained': [], 'edges': [],
           'low_quality': [], 'remainders': [], 'far_from_markers': [], 'near_band': [], 'repeated': []}
    per_pattern = {}
    for n in new_matches:
        per_pattern[n.pattern_id] = per_pattern.get(n.pattern_id, 0) + 1
    used = set()
    for o in old_matches:
        p = by_id.get(o.pattern_id)
        stored = p['duration'] if p else (o.end - o.start)
        base = {**ident, 'pattern_id': o.pattern_id, 'start': o.start, 'end': o.end,
                'old_score': o.confidence, 'pattern_s': stored}
        if o.pattern_id not in scope_ids:
            res['lost_to_scope'].append(base)
            continue
        group = [n for n in new_matches if n.pattern_id == o.pattern_id
                 and _overlap(n.start, n.end, o.start, o.end) > 0]
        covered = sum(_overlap(n.start, n.end, o.start, o.end) for n in group)
        span = min(o.end - o.start, sum(n.end - n.start for n in group)) if group else 0
        if group and covered >= 0.5 * span:
            res['pairs'] += 1
            used.update(id(n) for n in group)
            res['edges'].append({**base, **_edge_row(
                'old_match', (o.start, o.end), min(n.start for n in group), max(n.end for n in group))})
            if len(group) >= 2:
                group.sort(key=lambda n: n.start)
                res['split'].append({**base, 'pieces': len(group), 'dropped_s': sum(
                    max(0.0, b.start - a.end) for a, b in zip(group, group[1:], strict=False)),
                    'piece_coverage': [n.coverage for n in group]})
            continue
        ex = _explain(ep_ints, fp_duration, (o.pattern_id, p['ints'], p['duration'], p['sponsor'],
                                              p['category']), settings) if p and p['ints'] is not None else None
        superseded = any(n.pattern_id != o.pattern_id
                         and _overlap(n.start, n.end, o.start, o.end) > 0.5 * (o.end - o.start)
                         for n in new_matches)
        verdict, reason = classify_loss(o, ex, superseded)
        res['lost'].append({**base, 'bucket': 'under_10s' if stored < SHORT_PATTERN_S else 'over_10s',
                            'verdict': verdict, 'reason': reason,
                            'rejected_by': (ex or {}).get('rejected_by'), 'explain': ex})
    for n in new_matches:
        p = by_id.get(n.pattern_id)
        distance = _marker_distance(markers, n.start, n.end)
        row = {**ident, 'pattern_id': n.pattern_id, 'start': n.start, 'end': n.end,
               'similarity': n.similarity, 'coverage': n.coverage, 'contrast_margin': n.contrast_margin,
               'pattern_matches_in_episode': per_pattern[n.pattern_id], 'marker_distance_s': distance}
        if n.clipped_remainder:
            c_lo, c_hi = n.candidate_span
            res['remainders'].append({**row, 'candidate_start': c_lo, 'candidate_end': c_hi,
                                      'outside_candidate': n.start < c_lo - 1e-6 or n.end > c_hi + 1e-6})
        if distance is None or distance > FAR_FROM_MARKER_S:
            res['far_from_markers'].append(row)
        if NEAR_BAND[0] - 1e-9 <= n.similarity <= NEAR_BAND[1] + 1e-9:
            res['near_band'].append(row)
        if per_pattern[n.pattern_id] > 1:
            res['repeated'].append(row)
        marker = _best_marker(markers, n.start, n.end)
        if marker:
            res['edges'].append({**row, **_edge_row('marker', marker, n.start, n.end)})
        if n.similarity < LOW_SIMILARITY or n.coverage < LOW_COVERAGE:
            res['low_quality'].append(row)
        if id(n) in used:
            continue
        ex = _explain(ep_ints, fp_duration, (p['pattern_id'], p['ints'], p['duration'], p['sponsor'],
                                              p['category']), settings)
        close = None
        if 'close_evidence_s' in ex and _overlap(ex['start'], ex['end'], n.start, n.end) > 0.5 * (n.end - n.start):
            close = ex['close_evidence_s']
        res['gained'].append({**row, 'confirmed_by_marker': marker is not None, 'close_evidence_s': close})
    return res


def _counts(rows, key):
    out = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def _timing(values):
    if not values:
        return {'mean_s': None, 'p95_s': None, 'total_s': 0.0}
    return {'mean_s': float(np.mean(values)), 'p95_s': float(np.percentile(values, 95)),
            'total_s': float(np.sum(values))}


def pattern_report(patterns, settings, redact):
    """Matchability of every loaded pattern, with ceilings and self-match refusals by length."""
    min_cov = settings['fingerprint_min_coverage']
    unmatchable, refusals, undecodable = [], {'under_10s': 0, '10_to_30s': 0, 'over_30s': 0}, 0
    for p in patterns:
        if p['ints'] is None:
            undecodable += 1
            continue
        reason = af.fingerprint_unmatchable_reason(p['ints'], min_coverage=min_cov)
        if not reason:
            continue
        unmatchable.append({'pattern_id': p['pattern_id'], 'sponsor': redact.sponsor(p['sponsor']),
                            'pattern_s': p['duration'], 'reason': reason,
                            'coverage_ceiling': af.fingerprint_coverage_ceiling(p['ints'])})
        if reason.startswith('does not match its own audio'):
            refusals[_bucket(p['duration'])] += 1
    return {'loaded': len(patterns), 'undecodable': undecodable, 'unmatchable': unmatchable,
            'self_match_refusals': refusals}


def build_summary(results, skipped, fallback, scanned, patterns, settings, strictness_info, old_ref):
    def rows(key):
        return [r for res in results for r in res[key]]

    lost = rows('lost')
    superseded = [r for r in lost if r['verdict'] == 'superseded']
    gated = [r for r in lost if r['old_score'] >= GATE_SCORE - 1e-9 and r['verdict'] != 'superseded']
    blocking = [r for r in gated if r['verdict'] == 'unexplained']
    band = [r for r in lost if r['old_score'] < GATE_SCORE - 1e-9 and r['verdict'] != 'superseded']
    remainders = rows('remainders')
    outside = [r for r in remainders if r['outside_candidate']]
    gained = rows('gained')
    edges = rows('edges')
    by_gate = {}
    for r in lost:
        key = f"{r['rejected_by'] or 'none'}|{r['bucket']}"
        by_gate[key] = by_gate.get(key, 0) + 1
    known_close = [g for g in gained if g['close_evidence_s'] is not None]
    return {
        'old_ref': old_ref, 'episodes_scanned': scanned, 'episodes_skipped_no_original': skipped,
        'per_window_fallback_scans': fallback, 'settings': settings, 'strictness': strictness_info,
        'patterns': patterns,
        'old_matches': sum(r['old_count'] for r in results),
        'new_matches': sum(r['new_count'] for r in results),
        'matched_pairs': sum(r['pairs'] for r in results),
        'old_matches_out_of_scope': rows('lost_to_scope'),
        'lost': lost, 'lost_by_gate_and_length': by_gate,
        'gate_blocking': blocking, 'gate_explained': [r for r in gated if r['verdict'] == 'explained'],
        'superseded': superseded, 'band': band,
        'gained': {'total': len(gained), 'confirmed_by_marker': sum(g['confirmed_by_marker'] for g in gained),
                   'close_evidence_under_3s': sum(g['close_evidence_s'] < CLOSE_EVIDENCE_LIMIT_S
                                                  for g in known_close),
                   'close_evidence_3s_or_more': sum(g['close_evidence_s'] >= CLOSE_EVIDENCE_LIMIT_S
                                                    for g in known_close),
                   'close_evidence_unknown': len(gained) - len(known_close), 'rows': gained},
        'overshoot': [e for e in edges if e['overshoot']],
        'undershoot': [e for e in edges if e['undershoot']],
        'edge_rows_total': len(edges),
        'split': rows('split'), 'low_quality': rows('low_quality'),
        'far_from_markers': rows('far_from_markers'), 'near_band': rows('near_band'),
        'repeated': rows('repeated'),
        'timing': {'old': _timing([r['old_s'] for r in results]),
                   'new': _timing([r['new_s'] for r in results])},
        'remainders': {'count': len(remainders), 'seconds': sum(r['end'] - r['start'] for r in remainders),
                       'outside_candidate': len(outside), 'rows': remainders},
        'gate_passed': not blocking and not outside,
    }


def _fmt(v, nd=2):
    return '-' if v is None else f'{v:.{nd}f}'


def _table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|']
    out += ['| ' + ' | '.join(str(c) for c in row) + ' |' for row in rows]
    return out


def _loc(r):
    return [r['podcast'], r['episode'], r['pattern_id'], _fmt(r['start'], 1), _fmt(r['end'], 1)]


LOC_HEADERS = ['podcast', 'episode', 'pattern', 'start (s)', 'end (s)']


def render_markdown(s, redacted):
    L = ['# Fingerprint matcher comparison', '',
         ('Identifiers are hashed (--redact).' if redacted else
          'This report contains instance identifiers (podcast slugs, episode ids, sponsors). '
          'Do not paste it into public issues or PRs.'), '',
         f"Old matcher: git ref `{s['old_ref']}`. New matcher: working tree.", '', GATE_TEXT, '',
         f"## Gate result: {'PASS' if s['gate_passed'] else 'FAIL'}", '',
         f"- Unexplained losses at old score 0.70 or more: {len(s['gate_blocking'])}",
         f"- Explained losses at old score 0.70 or more (need acceptance): {len(s['gate_explained'])}",
         f"- Kept remainders outside their clipped match's span (must be 0): "
         f"{s['remainders']['outside_candidate']}",
         f"- Superseded losses (another pattern's match covers them, do not block): {len(s['superseded'])}",
         f"- Old matches 0.65 to 0.70 lost (verdict for the maintainer): {len(s['band'])}", '']
    if s['gate_blocking']:
        L += _table(LOC_HEADERS + ['old score', 'why unexplained'],
                    [_loc(r) + [_fmt(r['old_score']), r['reason']] for r in s['gate_blocking']]) + ['']
    if s['gate_explained']:
        L += ['Explained losses at 0.70 or more:', ''] + _table(
            LOC_HEADERS + ['old score', 'pattern (s)', 'rejecting gate'],
            [_loc(r) + [_fmt(r['old_score']), _fmt(r['pattern_s'], 1), r['rejected_by']]
             for r in s['gate_explained']]) + ['']
    st = s['strictness']
    p = s['patterns']
    L += ['## Scan', '',
          f"- Episodes scanned: {s['episodes_scanned']}",
          f"- Episodes skipped (no retained original): {s['episodes_skipped_no_original']}",
          f"- Per-window fallback scans (full-file fpcalc failed, episode not comparable): "
          f"{s['per_window_fallback_scans']}",
          f"- Stored strictness {'not set' if st['stored'] is None else _fmt(st['stored'], 3)} used as {_fmt(st['used'], 3)}; "
          f"clamped up to the range floor: {'yes' if st['clamped_up'] else 'no'}",
          f"- Settings used: {json.dumps(s['settings'], sort_keys=True)}", '',
          '## Patterns', '',
          f"- Patterns loaded: {p['loaded']} (undecodable: {p['undecodable']})",
          f"- Patterns with matchable false: {len(p['unmatchable'])}",
          '- Self-match refusals by stored length (count): '
          f"under 10 s = {p['self_match_refusals']['under_10s']}, "
          f"10 to 30 s = {p['self_match_refusals']['10_to_30s']}, "
          f"over 30 s = {p['self_match_refusals']['over_30s']}", '']
    if p['unmatchable']:
        L += _table(['pattern', 'sponsor', 'length (s)', 'coverage ceiling (ratio)', 'reason'],
                    [[u['pattern_id'], u['sponsor'], _fmt(u['pattern_s'], 1),
                      _fmt(u['coverage_ceiling']), u['reason']] for u in p['unmatchable']]) + ['']
    L += ['## Matches', '',
          f"- Old matches: {s['old_matches']} (out of scope for the episode: {len(s['old_matches_out_of_scope'])})",
          f"- New matches: {s['new_matches']}",
          f"- Matched pairs: {s['matched_pairs']}",
          f"- Old in-scope matches lost: {len(s['lost'])}", '']
    if s['lost_by_gate_and_length']:
        L += _table(['rejecting gate', 'stored length', 'lost matches (count)'],
                    [[k.split('|')[0], k.split('|')[1].replace('_', ' '), v]
                     for k, v in sorted(s['lost_by_gate_and_length'].items())]) + ['']
    g = s['gained']
    L += ['## Gained matches', '',
          f"- Gained: {g['total']}; confirmed by an existing ad marker overlapping more than half: "
          f"{g['confirmed_by_marker']}",
          f"- Close evidence under 3 s: {g['close_evidence_under_3s']}; 3 s or more: "
          f"{g['close_evidence_3s_or_more']}; not computable: {g['close_evidence_unknown']}", '']
    L += _table(LOC_HEADERS + ['similarity', 'coverage', 'contrast margin', 'marker', 'close evidence (s)'],
                [_loc(r) + [_fmt(r['similarity']), _fmt(r['coverage']), _fmt(r['contrast_margin'], 3),
                            'yes' if r['confirmed_by_marker'] else 'no', _fmt(r['close_evidence_s'])]
                 for r in g['rows']]) + [''] if g['rows'] else []
    rem = s['remainders']
    L += ['## Kept remainders', '',
          f"- Remainders of clipped matches kept: {rem['count']}, {_fmt(rem['seconds'], 1)} s in all; "
          f"outside their clipped match's span: {rem['outside_candidate']}", '']
    if rem['rows']:
        L += _table(LOC_HEADERS + ['clipped match start (s)', 'clipped match end (s)', 'outside'],
                    [_loc(r) + [_fmt(r['candidate_start'], 1), _fmt(r['candidate_end'], 1),
                                'yes' if r['outside_candidate'] else 'no'] for r in rem['rows']]) + ['']
    if s['superseded']:
        L += ['## Superseded losses', ''] + _table(
            LOC_HEADERS + ['old score', 'pattern (s)'],
            [_loc(r) + [_fmt(r['old_score']), _fmt(r['pattern_s'], 1)] for r in s['superseded']]) + ['']
    L += ['## Far from markers', '',
          f"- New matches more than {FAR_FROM_MARKER_S:.0f} s from every existing ad marker of the episode: "
          f"{len(s['far_from_markers'])}", '']
    L += _match_table(s['far_from_markers'], marker_distance=True)
    L += ['## Similarity 0.65 to 0.72', '',
          f"- New matches with similarity {NEAR_BAND[0]} to {NEAR_BAND[1]}: {len(s['near_band'])}", '']
    L += _match_table(s['near_band'])
    L += ['## Patterns matching more than once', '',
          f"- New matches of a pattern that matched more than once in the episode: {len(s['repeated'])}", '']
    L += _match_table(s['repeated'])
    L += ['## Edge moves', '',
          f"- Edge comparisons: {s['edge_rows_total']}; overshoot (more than 1 s outside): "
          f"{len(s['overshoot'])}; undershoot (more than 1 s inside): {len(s['undershoot'])}", '']
    if s['overshoot']:
        L += ['Overshoot candidates:', ''] + _table(
            LOC_HEADERS + ['against', 'start outside (s)', 'end outside (s)'],
            [_loc(r) + [r['ref'], _fmt(r['start_outside_s']), _fmt(r['end_outside_s'])]
             for r in s['overshoot']]) + ['']
    L += ['## Pieces', '', f"- Old matches that now return as 2 or more pieces: {len(s['split'])}", '']
    if s['split']:
        L += _table(LOC_HEADERS + ['pieces', 'dropped between pieces (s)', 'piece coverage (ratio)'],
                    [_loc(r) + [r['pieces'], _fmt(r['dropped_s']),
                                ', '.join(_fmt(c) for c in r['piece_coverage'])]
                     for r in s['split']]) + ['']
    L += ['## Listen to these', '',
          f"New matches with similarity under {LOW_SIMILARITY} or coverage under {LOW_COVERAGE}: "
          f"{len(s['low_quality'])}", '']
    if s['low_quality']:
        L += _table(LOC_HEADERS + ['similarity', 'coverage'],
                    [_loc(r) + [_fmt(r['similarity']), _fmt(r['coverage'])] for r in s['low_quality']]) + ['']
    if s['band']:
        L += ['## Old matches 0.65 to 0.70 lost', ''] + _table(
            LOC_HEADERS + ['old score', 'rejecting gate', 'explain_match (similarity, coverage, evidence s)', 'verdict'],
            [_loc(r) + [_fmt(r['old_score']), r['rejected_by'] or r['reason'],
                        _explain_cell(r.get('explain')), ''] for r in s['band']]) + ['']
    t = s['timing']
    L += ['## Wall time per episode', ''] + _table(
        ['matcher', 'mean (s)', 'p95 (s)', 'total (s)'],
        [[name, _fmt(t[name]['mean_s'], 3), _fmt(t[name]['p95_s'], 3), _fmt(t[name]['total_s'], 3)]
         for name in ('old', 'new')]) + [
        '', '## Notes', '',
        '- A match counts as paired when same-pattern new matches overlap the old match by at least '
        'half of the shorter span. The old match end is start plus stored length, so shorter new '
        'spans show as undershoot against the old match by design.',
        '- Old matches run over every active pattern (as shipped); new matches run over the '
        'in-scope patterns only. Old matches of out-of-scope patterns are listed in the JSON.',
        '- Close evidence and the explain columns come from the best alignment of the pattern in the '
        'episode, used only when it overlaps the match; otherwise the cell is "-".',
        '- Pieces are compared against the old span as one group; marker edge rows are per new match, '
        'so a piece of a split match can show as undershoot against the marker.', '']
    return '\n'.join(L)


def _match_table(rows, marker_distance=False):
    if not rows:
        return []
    headers = LOC_HEADERS + ['similarity', 'coverage', 'contrast margin', 'matches of the pattern in the episode']
    extra = ['nearest marker (s)'] if marker_distance else []
    return _table(headers + extra, [
        _loc(r) + [_fmt(r['similarity']), _fmt(r['coverage']), _fmt(r['contrast_margin'], 3),
                   r['pattern_matches_in_episode']]
        + ([_fmt(r['marker_distance_s'], 1)] if marker_distance else []) for r in rows]) + ['']


def _explain_cell(ex):
    if not ex or 'similarity' not in ex:
        return '-'
    return f"{_fmt(ex['similarity'])}, {_fmt(ex.get('coverage'))}, {_fmt(ex.get('evidence_s'), 1)}"


def run_compare(args, *, old_module=None, fp_provider=None, log=print):
    """Run the comparison and write the report; returns the exit code."""
    check_out_dir(args.out)
    for w in check_db_path(args.db):
        log(f"warning: {w}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    old_module = old_module or load_old_matcher(args.old_ref)
    old_matcher = old_module.AudioFingerprinter(db=None)
    provider = fp_provider or FpcalcProvider(out / 'fp-cache')
    redact = Redactor(args.redact)
    conn = open_readonly(args.db)
    try:
        settings, strictness_info = load_settings(conn)
        patterns = load_patterns(conn, af.decode_fingerprint_ints)
        sponsor_tags = load_sponsor_tags(conn)
        episodes = load_episodes(conn, args.podcast)
    finally:
        conn.close()
    ctx = {'redact': redact, 'settings': settings, 'patterns': patterns}
    results, skipped, fallback = [], 0, 0
    for row in episodes:
        if args.limit is not None and len(results) + fallback >= args.limit:
            break
        path = find_original(args.audio_root, row['slug'], row['episode_id'])
        if path is None:
            skipped += 1
            continue
        fp = provider(path)
        if fp is None:
            fallback += 1
            continue
        try:
            tags = set(json.loads(row['podcast_tags'] or '[]'))
        except (TypeError, ValueError):
            tags = set()
        ep = {'slug': row['slug'], 'episode_id': row['episode_id'], 'markers': _markers(row['ad_markers_json']),
              'scope_ids': {p['pattern_id'] for p in patterns if pattern_passes_scope(
                  p['scope'], p['network_id'], p['podcast_id'], p['source'],
                  sponsor_tags.get(p['sponsor_id'], set()), podcast_id=row['slug'],
                  network_id=row['network'], podcast_tag_set=tags)}}
        ints, fp_duration, total = fp
        log(f"episode {redact.slug(row['slug'])}/{redact.episode(row['episode_id'])}")
        results.append(compare_episode(ctx, ep, ints, fp_duration or total, total, old_matcher))
    summary = build_summary(results, skipped, fallback, len(results) + fallback, pattern_report(
        patterns, settings, redact), settings, strictness_info, args.old_ref)
    (out / 'report.json').write_text(json.dumps(summary, indent=2, default=str))
    (out / 'report.md').write_text(render_markdown(summary, args.redact))
    log(GATE_TEXT)
    log(f"Gate {'PASSED' if summary['gate_passed'] else 'FAILED'}: "
        f"{len(summary['gate_blocking'])} unexplained loss(es) at old score 0.70 or more, "
        f"{len(summary['gate_explained'])} explained (need acceptance), "
        f"{summary['remainders']['outside_candidate']} kept remainder(s) outside their clipped match, "
        f"{len(summary['superseded'])} superseded, {len(summary['band'])} in the 0.65 to 0.70 band")
    log(f"Report: {out / 'report.md'}")
    return 0 if summary['gate_passed'] else 2


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', required=True, help='copy of the instance SQLite database (opened read-only)')
    ap.add_argument('--audio-root', required=True, help='directory holding <slug>/episodes/<id>-original.*')
    ap.add_argument('--old-ref', default=DEFAULT_OLD_REF, help='git ref of the old matcher')
    ap.add_argument('--limit', type=int, help='scan at most this many episodes')
    ap.add_argument('--podcast', help='only this podcast slug')
    ap.add_argument('--out', help='report directory (default ./fingerprint-compare-<timestamp>)')
    ap.add_argument('--redact', action='store_true', help='hash slugs, episode ids and sponsors')
    args = ap.parse_args(argv)
    args.out = args.out or str(Path.cwd() / f"fingerprint-compare-{datetime.now():%Y%m%d-%H%M%S}")
    return args


def main(argv=None):
    try:
        return run_compare(parse_args(argv))
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        raise
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
