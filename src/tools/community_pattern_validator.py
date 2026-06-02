"""Community pattern submission validator.

Runs schema, tag, sponsor, quality, and dedupe checks against one or more
JSON pattern files. Same code is used by:

  - CI (`python -m src.tools.community_pattern_validator --pr-files A.json B.json`)
  - The `POST /patterns/import` endpoint for community-format imports

Outputs a structured ValidationResult per file plus a single combined
Markdown comment for posting back to the PR. CLI exits non-zero when any
file fails a hard check (rejected) so the GitHub Action sets a red status.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Defensive sys.path bootstrap so direct `python path/to/script.py` invocation
# works as well as `python -m src.tools.X` (the workflow-style invocation).
_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from community_export import find_foreign_sponsors  # noqa: E402
from utils.community_tags import (  # noqa: E402
    BUNDLE_FORMAT,
    BUNDLE_NAME_PREFIX,
    CANONICAL_DAYS,
    CANONICAL_MONTHS,
    CANONICAL_RELATIVE_TIME,
    CANONICAL_STOPWORDS,
    DATE_REGEX,
    DOMAIN_TLDS,
    TRAILING_TRUNCATION_STOPWORDS,
    YEAR_REGEX,
    expected_filename,
    iter_bundle_patterns,
    sponsor_seed,
    valid_tags,
)

logger = logging.getLogger('community_pattern_validator')

DUPLICATE_THRESHOLD = 0.95
VARIANT_THRESHOLD = 0.75

REQUIRED_FIELDS = ('community_id', 'text_template', 'sponsor', 'version', 'submitted_at')


@dataclass
class ValidationResult:
    path: str
    status: str = 'pass'  # 'pass' | 'reject' | 'warn'
    sponsor: Optional[str] = None
    sponsor_match: str = 'unknown'  # 'exact' | 'alias' | 'fuzzy' | 'unknown'
    classification: str = 'distinct'  # 'duplicate' | 'variant' | 'distinct'
    similar_to: Optional[str] = None  # community_id
    similarity: Optional[float] = None
    diff_snippet: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def canonicalize_for_dedupe(text: str) -> str:
    """Return the canonical form of `text` used for dedupe comparison only.

    Mirrors plan section 8.5: lowercase -> strip date/year tokens (BEFORE
    punctuation removal, otherwise '12/31' becomes '12 31' and slips past)
    -> punctuation->space -> collapse whitespace -> strip stopwords / day
    / month / relative-time tokens -> trim. Original text is not modified.
    """
    if not text:
        return ''
    s = text.lower()
    # Date-format strings and 4-digit years must be removed BEFORE punctuation
    # → space, otherwise '12/31' becomes '12 31' and slips through the date regex.
    s = DATE_REGEX.sub(' ', s)
    s = YEAR_REGEX.sub(' ', s)
    s = re.sub(r'[^a-z0-9\s]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return ''
    tokens = s.split(' ')
    drop = CANONICAL_STOPWORDS | CANONICAL_DAYS | CANONICAL_MONTHS | CANONICAL_RELATIVE_TIME
    kept = [t for t in tokens if t and t not in drop]
    return ' '.join(kept)


def similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two canonicalized strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _classify_sponsor(sponsor_name: str, seed: List[Dict[str, Any]]) -> str:
    if not sponsor_name:
        return 'unknown'
    lname = sponsor_name.lower()
    for s in seed:
        if s['name'].lower() == lname:
            return 'exact'
        for alias in s.get('aliases') or []:
            if alias.lower() == lname:
                return 'alias'
    for s in seed:
        nm = s['name'].lower()
        if nm and (nm in lname or lname in nm):
            return 'fuzzy'
    return 'unknown'


def _schema_errors(doc: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not isinstance(doc, dict):
        return ['payload must be a JSON object']
    for k in REQUIRED_FIELDS:
        if not doc.get(k):
            errs.append(f'missing required field: {k}')
    if doc.get('version') is not None:
        try:
            int(doc['version'])
        except (TypeError, ValueError):
            errs.append('version must be an integer')
    if doc.get('submitted_at') and not isinstance(doc['submitted_at'], str):
        # A non-string submitted_at passes the truthiness check above but later
        # crashes the manifest generator's sort (int vs str), DoS-ing manifest
        # regeneration repo-wide (tools-cli-2).
        errs.append('submitted_at must be a string')
    if doc.get('text_template') and not isinstance(doc['text_template'], str):
        errs.append('text_template must be a string')
    if doc.get('intro_variants') is not None and not isinstance(doc['intro_variants'], list):
        errs.append('intro_variants must be a list')
    if doc.get('outro_variants') is not None and not isinstance(doc['outro_variants'], list):
        errs.append('outro_variants must be a list')
    if doc.get('sponsor_tags') is not None and not isinstance(doc['sponsor_tags'], list):
        errs.append('sponsor_tags must be a list')
    return errs


def _tag_errors(doc: Dict[str, Any]) -> List[str]:
    vt = valid_tags()
    bad = [t for t in (doc.get('sponsor_tags') or []) if t not in vt]
    return [f'unknown tag: {t}' for t in bad]


def _quality_errors(doc: Dict[str, Any]) -> List[str]:
    """Defense-in-depth: re-run the same quality gates from the export side."""
    errs: List[str] = []
    text = doc.get('text_template') or ''
    if len(text) < 50:
        errs.append(f'text_template too short ({len(text)} < 50)')
    if len(text) > 3500:
        errs.append(f'text_template too long ({len(text)} > 3500)')
    dur = doc.get('avg_duration')
    if isinstance(dur, (int, float)) and dur > 120:
        errs.append(f'avg_duration too long ({dur:.0f}s > 120s)')

    sponsor = (doc.get('sponsor') or '').lower()
    text_l = text.lower()
    aliases = [a.lower() for a in (doc.get('sponsor_aliases') or [])]
    candidates = [sponsor] + aliases
    if not any(c and re.search(rf'\b{re.escape(c)}\b', text_l) for c in candidates):
        errs.append('sponsor (or any alias) does not appear in text_template')
    return errs


def _filename_errors(path: str, doc: Dict[str, Any]) -> List[str]:
    """Reject when the on-disk filename does not equal slugify(sponsor)-<short>.json.

    Caught the v2.5.x PR #292 footgun where a contributor hand-edited the
    `sponsor` field but did not rename the file (or where the local DB had a
    sponsor classification that disagreed with the ad text).

    Skipped when `community_id` is empty -- the required-field check already
    rejects that and a duplicate message would be noise.
    """
    actual = Path(path).name
    community_id = doc.get('community_id') or ''
    if not community_id:
        return []
    expected = expected_filename(doc.get('sponsor') or '', community_id)
    if expected is None or actual == expected:
        return []
    return [
        f'filename mismatch: file is named "{actual}" but sponsor '
        f'"{doc.get("sponsor")}" + community_id "{community_id[:8]}..." '
        f'requires "{expected}". Rename the file or fix the sponsor field.'
    ]


def _truncation_warnings(doc: Dict[str, Any]) -> List[str]:
    """Warn when an intro/outro variant looks cut mid-clause.

    Heuristic: strip trailing punctuation, split into tokens; flag the variant
    if the last token is in a stopword set ("the", "at", "and", "com", "slash",
    ...) or is a single non-"i" letter. Exception: a "dot <tld>" tail
    (e.g. "shopify dot com") is treated as a completed URL and NOT flagged.
    Variants are recall boosters, so a stray fragment never matches anything
    and just clutters the pattern. Warning only -- the variant could still be
    a legitimate anchor.
    """
    warnings: List[str] = []
    for kind in ('intro_variants', 'outro_variants'):
        for i, v in enumerate(doc.get(kind) or []):
            if not isinstance(v, str):
                continue
            # Strip ASCII + curly quotes / ellipsis so Whisper output with
            # smart quotes does not hide the real trailing token.
            # Escape forms keep source ASCII-only per the repo lint rule.
            stripped = v.rstrip(' .,;!?"\'\u2018\u2019\u201c\u201d\u2026')
            if not stripped:
                continue
            tokens = stripped.split()
            if not tokens:
                continue
            last = tokens[-1].lower()
            prev = tokens[-2].lower() if len(tokens) >= 2 else ''
            if prev == 'dot' and last in DOMAIN_TLDS:
                continue
            if last in TRAILING_TRUNCATION_STOPWORDS:
                tail = ' '.join(tokens[-2:])
                warnings.append(
                    f'{kind}[{i}] looks truncated -- ends with "{tail}". '
                    f'Trim, drop, or extend the variant.'
                )
                continue
            if len(last) == 1 and last != 'i':
                warnings.append(
                    f'{kind}[{i}] looks truncated -- ends with single letter "{last}". '
                    f'Trim, drop, or extend the variant.'
                )
    return warnings


def _file_shape_warnings(path: str, raw: Dict[str, Any]) -> List[str]:
    """Surface filename / payload-shape mismatches.

    - Bundle payload in a `<slug>-<short>.json`-shaped file: probably a hand
      split that forgot to flatten; the contained patterns will validate
      individually but the directory convention is per-pattern files.
    - Per-pattern payload in a `minuspod-submission-*.json`-shaped file: the
      contributor dropped a bundle filename on a single pattern; the manifest
      generator will still pick it up, but the directory convention should
      hold.

    Warnings, not rejections -- both shapes already parse correctly downstream.
    """
    if not isinstance(raw, dict):
        return []
    name = Path(path).name
    is_bundle_payload = raw.get('format') == BUNDLE_FORMAT
    is_bundle_name = name.startswith(BUNDLE_NAME_PREFIX)
    if is_bundle_payload and not is_bundle_name:
        return [
            f'shape mismatch: "{name}" looks like a per-pattern filename '
            f'but the payload is a bundle (format={BUNDLE_FORMAT}). '
            f'Either rename it to "{BUNDLE_NAME_PREFIX}<id>.json" or use '
            '`python -m src.tools.split_bundle` to land it as per-pattern files.'
        ]
    if not is_bundle_payload and is_bundle_name:
        return [
            f'shape mismatch: "{name}" looks like a bundle filename but the '
            'payload is a per-pattern document. Rename to '
            '`<slug>-<short_uuid>.json` (see patterns/CONTRIBUTING.md).'
        ]
    return []


def _single_pattern_errors(doc: Dict[str, Any], seed: List[Dict[str, Any]]) -> List[str]:
    """Reject submissions that stitch multiple ads together.

    Delegates to the shared `find_foreign_sponsors` helper so the import
    side and export side agree on the rule.
    """
    text = doc.get('text_template') or ''
    declared = (doc.get('sponsor') or '').lower()
    declared_aliases = {a.lower() for a in (doc.get('sponsor_aliases') or [])}
    declared_lower = {declared, *declared_aliases} - {''}

    foreign = find_foreign_sponsors(text, declared_lower, seed)
    if not foreign:
        return []
    sample = ', '.join(foreign[:3])
    more = '' if len(foreign) <= 3 else f' (+{len(foreign) - 3} more)'
    return [
        'multi-sponsor block: text mentions other seed sponsors '
        f'({sample}{more}). Each community pattern must describe ONE ad.'
    ]


def _diff_snippet(incoming: str, existing: str, n: int = 200) -> str:
    """Return a short side-by-side diff snippet capped at `n` chars per side."""
    inc = incoming[:n].replace('\n', ' ')
    exi = existing[:n].replace('\n', ' ')
    return f'incoming: "{inc}"\nexisting: "{exi}"'


def dedupe(doc: Dict[str, Any], existing: List[Dict[str, Any]]) -> Tuple[str, Optional[Dict[str, Any]], float]:
    """Compare `doc` against `existing` patterns sharing the same sponsor.

    Returns (classification, matched_existing_doc_or_None, best_score).
    classification is 'duplicate' (>=95%), 'variant' (75-95%), or 'distinct'.
    """
    sponsor = (doc.get('sponsor') or '').lower()
    if not sponsor:
        return 'distinct', None, 0.0
    incoming_canon = canonicalize_for_dedupe(doc.get('text_template') or '')
    if not incoming_canon:
        return 'distinct', None, 0.0

    best_score = 0.0
    best_match: Optional[Dict[str, Any]] = None
    for ex in existing:
        if (ex.get('sponsor') or '').lower() != sponsor:
            continue
        ex_canon = canonicalize_for_dedupe(ex.get('text_template') or '')
        score = similarity(incoming_canon, ex_canon)
        if score > best_score:
            best_score = score
            best_match = ex

    if best_score >= DUPLICATE_THRESHOLD:
        return 'duplicate', best_match, best_score
    if best_score >= VARIANT_THRESHOLD:
        return 'variant', best_match, best_score
    return 'distinct', best_match, best_score


def validate_doc(
    path: str,
    doc: Dict[str, Any],
    seed: List[Dict[str, Any]],
    existing: List[Dict[str, Any]],
) -> ValidationResult:
    """Validate a single pattern doc against the seed list and existing patterns."""
    result = ValidationResult(path=path, sponsor=doc.get('sponsor'))

    schema_errs = _schema_errors(doc)
    if schema_errs:
        result.errors.extend(schema_errs)
        result.status = 'reject'
        return result

    # Strip bundle entry suffix ("file.json#patterns[3]") so the filename
    # check inspects the actual on-disk path. Bundle entries skip the
    # check entirely -- bundles are validated as one file, not per-entry.
    if '#patterns[' not in path:
        fname_errs = _filename_errors(path, doc)
        if fname_errs:
            result.errors.extend(fname_errs)
            result.status = 'reject'

    tag_errs = _tag_errors(doc)
    if tag_errs:
        result.errors.extend(tag_errs)
        result.status = 'reject'

    quality_errs = _quality_errors(doc)
    if quality_errs:
        result.errors.extend(quality_errs)
        result.status = 'reject'

    single_errs = _single_pattern_errors(doc, seed)
    if single_errs:
        result.errors.extend(single_errs)
        result.status = 'reject'

    result.sponsor_match = _classify_sponsor(doc.get('sponsor') or '', seed)
    if result.sponsor_match == 'unknown':
        result.warnings.append(
            f'new sponsor "{doc.get("sponsor")}" -- not yet in seed list '
            '(informational; community submissions can introduce new sponsors)'
        )

    if result.status == 'reject':
        return result

    classification, matched, score = dedupe(doc, existing)
    result.classification = classification
    result.similarity = round(score, 3) if score else None
    if matched:
        result.similar_to = matched.get('community_id')
        if classification == 'duplicate':
            result.errors.append(
                f'duplicates {matched.get("community_id")} (score={score:.2f})'
            )
            result.status = 'reject'
        elif classification == 'variant':
            result.warnings.append(
                f'similar to {matched.get("community_id")} at {score:.0%}; '
                f'consider merging into existing intro/outro variants'
            )
            result.diff_snippet = _diff_snippet(
                doc.get('text_template') or '',
                matched.get('text_template') or '',
            )

    trunc_warns = _truncation_warnings(doc)
    if trunc_warns:
        result.warnings.extend(trunc_warns)

    if result.warnings and result.status != 'reject':
        result.status = 'warn'
    return result


def _extract_patterns(path: str, raw: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Yield (synthetic_path, pattern_doc) for any payload shape.

    A flat per-pattern file returns ``[(path, raw)]``. A bundle file returns
    one entry per pattern in the ``patterns`` array, with ``path#patterns[i]``
    so the PR comment can point at the failing index.
    """
    if not isinstance(raw, dict):
        return []
    if raw.get('format') == BUNDLE_FORMAT:
        return [
            (f'{path}#patterns[{i}]', p)
            for i, p in enumerate(iter_bundle_patterns(raw))
        ]
    return [(path, raw)]


def _load_existing_patterns(community_dir: Path) -> List[Dict[str, Any]]:
    """Load every JSON file in patterns/community/ (recursive) as existing patterns.

    Bundle files contribute every pattern in their ``patterns[]`` array.
    """
    out: List[Dict[str, Any]] = []
    if not community_dir.exists():
        return out
    for p in community_dir.rglob('*.json'):
        if p.name == 'index.json':
            continue
        try:
            with p.open('r', encoding='utf-8') as fh:
                raw = json.load(fh)
        except Exception as e:
            logger.warning(f'Could not parse {p}: {e}')
            continue
        for _, doc in _extract_patterns(str(p), raw):
            out.append(doc)
    return out


def render_markdown_comment(results: List[ValidationResult]) -> str:
    """Render a single Markdown comment for the PR summarizing all results.

    Each section links back to the relevant part of `patterns/CONTRIBUTING.md`
    so submitters can self-serve on what failed and why.
    """
    contributing = '../blob/main/patterns/CONTRIBUTING.md'
    quality_link = f'[Quality checks]({contributing}#quality-checks-before-submission)'
    dedupe_link = f'[Dedupe]({contributing}#dedupe)'
    sponsor_link = (
        '[How to add a sponsor to the seed list]'
        '(../blob/main/patterns/README.md#how-to-add-a-sponsor-to-the-seed-list)'
    )

    lines: List[str] = ['## Community pattern validation', '']
    rejected = [r for r in results if r.status == 'reject']
    warned = [r for r in results if r.status == 'warn']
    passed = [r for r in results if r.status == 'pass']

    if rejected:
        lines.append(f'### Rejected ({len(rejected)})')
        lines.append(f'See {quality_link} and {dedupe_link} for what each gate enforces.')
        lines.append('')
        for r in rejected:
            lines.append(f'- `{r.path}` (sponsor: {r.sponsor})')
            for e in r.errors:
                lines.append(f'  - {e}')
        lines.append('')
    if warned:
        lines.append(f'### Warnings ({len(warned)})')
        lines.append(
            f'Variant suggestions ({dedupe_link}) are advisory -- the '
            f'maintainer decides during review. New sponsors are expected '
            f'and welcome; see {sponsor_link} if you want to canonicalize.'
        )
        lines.append('')
        for r in warned:
            lines.append(f'- `{r.path}` (sponsor: {r.sponsor})')
            for w in r.warnings:
                lines.append(f'  - {w}')
            if r.diff_snippet:
                lines.append('  - Diff:')
                lines.append('    ```')
                for ln in r.diff_snippet.splitlines():
                    lines.append(f'    {ln}')
                lines.append('    ```')
        lines.append('')
    if passed:
        lines.append(f'### Passed ({len(passed)})')
        for r in passed:
            lines.append(f'- `{r.path}` (sponsor: {r.sponsor})')
        lines.append('')

    if not (rejected or warned):
        lines.append('Validation passed. Ready for review.')

    lines.append('')
    lines.append(
        f'_See [`patterns/CONTRIBUTING.md`]({contributing}) for the full '
        f'submission guide._'
    )
    return '\n'.join(lines).rstrip() + '\n'


def run(pr_files: List[str], comment_output: Optional[str] = None,
        status_output: Optional[str] = None) -> int:
    """CLI driver. Returns 0 when no rejections, 1 otherwise."""
    seed = sponsor_seed()
    repo_root = _REPO_SRC.parent
    existing = _load_existing_patterns(repo_root / 'patterns' / 'community')

    # CI checks out the PR branch, so files added in the PR are already on
    # disk in patterns/community/. Strip them from the "existing" baseline
    # by community_id, otherwise dedupe sees every new file as a duplicate
    # of itself with score=1.00.
    pr_paths = {Path(p).resolve() for p in pr_files}
    pr_community_ids: set = set()
    for p in pr_paths:
        if not p.exists():
            continue
        try:
            with p.open('r', encoding='utf-8') as fh:
                raw = json.load(fh)
        except Exception:
            continue
        for _, doc in _extract_patterns(str(p), raw):
            cid = doc.get('community_id')
            if cid:
                pr_community_ids.add(cid)
    if pr_community_ids:
        existing = [e for e in existing if e.get('community_id') not in pr_community_ids]

    results: List[ValidationResult] = []
    for path in pr_files:
        p = Path(path)
        if not p.exists():
            results.append(ValidationResult(
                path=path, status='reject',
                errors=[f'file not found: {path}'],
            ))
            continue
        try:
            with p.open('r', encoding='utf-8') as fh:
                raw = json.load(fh)
        except Exception as e:
            results.append(ValidationResult(
                path=path, status='reject',
                errors=[f'JSON parse error: {e}'],
            ))
            continue
        if p.name == 'index.json':
            continue
        extracted = _extract_patterns(path, raw)
        if not extracted:
            results.append(ValidationResult(
                path=path, status='reject',
                errors=['empty submission: no patterns found in file'],
            ))
            continue
        shape_warns = _file_shape_warnings(path, raw)
        if shape_warns:
            results.append(ValidationResult(
                path=path, status='warn', sponsor='(file)',
                warnings=list(shape_warns),
            ))
        for sub_path, doc in extracted:
            results.append(validate_doc(sub_path, doc, seed, existing))

    markdown = render_markdown_comment(results)
    if comment_output:
        Path(comment_output).write_text(markdown, encoding='utf-8')
    if status_output:
        statuses = ','.join(f'{r.path}:{r.status}' for r in results)
        Path(status_output).write_text(statuses + '\n', encoding='utf-8')

    rejected = any(r.status == 'reject' for r in results)
    print(markdown)
    return 1 if rejected else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Validate community pattern submissions.')
    parser.add_argument('--pr-files', nargs='+', required=True,
                        help='One or more JSON file paths from the PR diff.')
    parser.add_argument('--comment-output', default=None,
                        help='Where to write the Markdown comment.')
    parser.add_argument('--status-output', default=None,
                        help='Where to write a one-line status string.')
    args = parser.parse_args(argv)
    return run(args.pr_files, args.comment_output, args.status_output)


if __name__ == '__main__':
    sys.exit(main())
