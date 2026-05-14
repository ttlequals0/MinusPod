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

# Allow `python -m` style invocation by ensuring repo src/ is on sys.path.
_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from utils.community_tags import (  # noqa: E402
    CANONICAL_DAYS,
    CANONICAL_MONTHS,
    CANONICAL_RELATIVE_TIME,
    CANONICAL_STOPWORDS,
    DATE_REGEX,
    YEAR_REGEX,
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

    tag_errs = _tag_errors(doc)
    if tag_errs:
        result.errors.extend(tag_errs)
        result.status = 'reject'

    quality_errs = _quality_errors(doc)
    if quality_errs:
        result.errors.extend(quality_errs)
        result.status = 'reject'

    result.sponsor_match = _classify_sponsor(doc.get('sponsor') or '', seed)
    if result.sponsor_match == 'unknown':
        result.warnings.append(
            f'sponsor "{doc.get("sponsor")}" not in seed list (triage required)'
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

    if result.warnings and result.status != 'reject':
        result.status = 'warn'
    return result


def _load_existing_patterns(community_dir: Path) -> List[Dict[str, Any]]:
    """Load every JSON file in patterns/community/ (recursive) as an existing pattern."""
    out: List[Dict[str, Any]] = []
    if not community_dir.exists():
        return out
    for p in community_dir.rglob('*.json'):
        if p.name == 'index.json':
            continue
        try:
            with p.open('r', encoding='utf-8') as fh:
                out.append(json.load(fh))
        except Exception as e:
            logger.warning(f'Could not parse {p}: {e}')
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
            f'Variant suggestions ({dedupe_link}) and unknown-sponsor flags '
            f'({sponsor_link}) are advisory — the maintainer decides during review.'
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
                doc = json.load(fh)
        except Exception as e:
            results.append(ValidationResult(
                path=path, status='reject',
                errors=[f'JSON parse error: {e}'],
            ))
            continue
        # Skip the manifest file if it gets passed in.
        if p.name == 'index.json':
            continue
        results.append(validate_doc(path, doc, seed, existing))

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
