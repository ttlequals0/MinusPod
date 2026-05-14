"""Community pattern export pipeline.

Given a local pattern's id, runs quality gates, PII stripping, metadata
stripping, sponsor classification, and produces a JSON document suitable
for submission to the MinusPod patterns/community/ directory in the
upstream GitHub repository.

Returns a structured dict the API layer can hand to the frontend.

Pipeline (mirrors the plan, section 7):

1. Quality gates
2. Tag validation
3. PII strip (consumer emails by domain whitelist; phone numbers, keep
   toll-free, strip all else)
4. Metadata strip
5. Sponsor name classification (exact / alias / fuzzy / unknown)
6. Generate fresh fields (community_id, version=1, submitted_at,
   submitted_app_version)
7. JSON output
8. Prefilled GitHub PR URL with a 7KB fallback to file-download
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from utils.community_tags import (
    CONSUMER_EMAIL_DOMAINS,
    EMAIL_REGEX,
    PHONE_REGEX,
    is_tollfree,
    valid_tags,
)

logger = logging.getLogger('podcast.community_export')

MIN_TEXT_LEN = 50
MAX_TEXT_LEN = 3500
MAX_DURATION_SECONDS = 120
URL_LENGTH_LIMIT_BYTES = 7 * 1024  # 7 KB

GITHUB_REPO = 'ttlequals0/MinusPod'
PR_URL_TEMPLATE = (
    'https://github.com/{repo}/new/main/patterns/community'
    '?filename={filename}&value={value}'
)


class ExportError(Exception):
    """Raised when a pattern fails the export pipeline."""

    def __init__(self, reasons: List[str]):
        super().__init__('; '.join(reasons))
        self.reasons = reasons


def _slugify(name: str) -> str:
    """Lowercase, hyphenated, ASCII-safe slug."""
    s = re.sub(r'[^a-z0-9]+', '-', name.lower())
    return s.strip('-') or 'sponsor'


def _strip_emails(text: str) -> str:
    """Strip consumer-domain emails from a text body. Returns the cleaned text.

    Business addresses (anything not in CONSUMER_EMAIL_DOMAINS) are kept.
    """
    def _sub(m: re.Match) -> str:
        domain = m.group(2).lower()
        if domain in CONSUMER_EMAIL_DOMAINS:
            return '[email]'
        return m.group(0)

    return EMAIL_REGEX.sub(_sub, text)


def _strip_phones(text: str) -> str:
    """Strip non-toll-free phone numbers from a text body."""
    def _sub(m: re.Match) -> str:
        phone = m.group(0)
        return phone if is_tollfree(phone) else '[phone]'

    return PHONE_REGEX.sub(_sub, text)


def strip_pii(text: str) -> str:
    """Apply email + phone PII stripping in order."""
    if not text:
        return text
    return _strip_phones(_strip_emails(text))


def _quality_gates(pattern: Dict, sponsors: List[Dict]) -> List[str]:
    """Run quality gates. Returns a list of failure reasons (empty = pass)."""
    reasons: List[str] = []
    text = pattern.get('text_template') or ''

    if len(text) < MIN_TEXT_LEN:
        reasons.append(f'text_template too short ({len(text)} < {MIN_TEXT_LEN})')
    if len(text) > MAX_TEXT_LEN:
        reasons.append(f'text_template too long ({len(text)} > {MAX_TEXT_LEN})')

    duration = pattern.get('avg_duration') or 0
    if duration and duration > MAX_DURATION_SECONDS:
        reasons.append(f'avg_duration too long ({duration:.0f}s > {MAX_DURATION_SECONDS}s)')

    if (pattern.get('confirmation_count') or 0) < 1:
        reasons.append('confirmation_count must be >= 1')

    fp = pattern.get('false_positive_count') or 0
    cc = pattern.get('confirmation_count') or 0
    if fp > cc:
        reasons.append(f'false_positive_count ({fp}) > confirmation_count ({cc})')

    sponsor_id = pattern.get('sponsor_id')
    if not sponsor_id:
        reasons.append('sponsor_id is required')
        return reasons

    sponsor_row = next((s for s in sponsors if s['id'] == sponsor_id), None)
    if not sponsor_row:
        reasons.append('sponsor not found')
        return reasons

    sponsor_names = [sponsor_row['name']]
    try:
        aliases = json.loads(sponsor_row.get('aliases') or '[]')
        if isinstance(aliases, list):
            sponsor_names.extend(aliases)
    except (TypeError, ValueError):
        pass

    text_lower = text.lower()
    # Whitespace-bounded, case-insensitive presence test.
    name_present = any(
        re.search(rf'\b{re.escape(n.lower())}\b', text_lower)
        for n in sponsor_names if n
    )
    if not name_present:
        reasons.append('sponsor name (or any alias) does not appear in text_template')

    # Foreign sponsor presence test: no other sponsor's name should appear.
    foreign = []
    for s in sponsors:
        if s['id'] == sponsor_id or not s.get('is_active'):
            continue
        other_names = [s['name']]
        try:
            other_aliases = json.loads(s.get('aliases') or '[]')
            if isinstance(other_aliases, list):
                other_names.extend(other_aliases)
        except (TypeError, ValueError):
            pass
        for name in other_names:
            if not name:
                continue
            if re.search(rf'\b{re.escape(name.lower())}\b', text_lower):
                foreign.append(name)
                break
    if foreign:
        reasons.append(f'foreign sponsor names appear in text: {", ".join(foreign[:3])}')

    return reasons


def _validate_tags(pattern: Dict, sponsor_row: Dict) -> List[str]:
    """Reject any tag not in VALID_TAGS."""
    bad: List[str] = []
    vt = valid_tags()
    try:
        tags = json.loads(sponsor_row.get('tags') or '[]')
    except (TypeError, ValueError):
        tags = []
    for t in tags or []:
        if t not in vt:
            bad.append(t)
    return [f'unknown tag: {t}' for t in bad]


def _classify_sponsor(sponsor_name: str, sponsors: List[Dict]) -> str:
    """Classify how the sponsor maps to the seed list: exact|alias|fuzzy|unknown."""
    if not sponsor_name:
        return 'unknown'
    lname = sponsor_name.lower()
    for s in sponsors:
        if s.get('name', '').lower() == lname:
            return 'exact'
        try:
            aliases = json.loads(s.get('aliases') or '[]')
        except (TypeError, ValueError):
            aliases = []
        for a in aliases or []:
            if a.lower() == lname:
                return 'alias'
    # Cheap fuzzy: substring match in either direction.
    for s in sponsors:
        nm = s.get('name', '').lower()
        if nm and (nm in lname or lname in nm):
            return 'fuzzy'
    return 'unknown'


def _strip_metadata(pattern: Dict, sponsor_row: Dict) -> Dict:
    """Build the export payload, omitting fields the plan lists as stripped."""
    try:
        intro_variants = json.loads(pattern.get('intro_variants') or '[]')
    except (TypeError, ValueError):
        intro_variants = []
    try:
        outro_variants = json.loads(pattern.get('outro_variants') or '[]')
    except (TypeError, ValueError):
        outro_variants = []
    try:
        sponsor_tags = json.loads(sponsor_row.get('tags') or '[]')
    except (TypeError, ValueError):
        sponsor_tags = []
    try:
        sponsor_aliases = json.loads(sponsor_row.get('aliases') or '[]')
    except (TypeError, ValueError):
        sponsor_aliases = []

    # Apply PII strip to text_template and variants before serializing.
    text_template = strip_pii(pattern.get('text_template') or '')
    intro_variants = [strip_pii(v) for v in intro_variants if isinstance(v, str)]
    outro_variants = [strip_pii(v) for v in outro_variants if isinstance(v, str)]

    return {
        'scope': pattern.get('scope') or 'global',
        'text_template': text_template,
        'intro_variants': intro_variants,
        'outro_variants': outro_variants,
        'avg_duration': pattern.get('avg_duration'),
        'sponsor': sponsor_row.get('name'),
        'sponsor_aliases': sponsor_aliases,
        'sponsor_tags': sponsor_tags,
    }


def _app_version() -> str:
    try:
        from version import __version__
        return __version__
    except Exception:
        return 'unknown'


def build_export_payload(pattern: Dict, sponsors: List[Dict]) -> Dict:
    """Run the full pipeline and return the JSON payload + sponsor classification."""
    sponsor_id = pattern.get('sponsor_id')
    sponsor_row = next((s for s in sponsors if s['id'] == sponsor_id), None)

    failures = _quality_gates(pattern, sponsors)
    if sponsor_row:
        failures.extend(_validate_tags(pattern, sponsor_row))
    if failures:
        raise ExportError(failures)

    payload = _strip_metadata(pattern, sponsor_row)

    sponsor_match = _classify_sponsor(payload['sponsor'], sponsors)

    payload.update({
        'community_id': str(uuid.uuid4()),
        'version': 1,
        'submitted_at': datetime.now(timezone.utc).isoformat(),
        'submitted_app_version': _app_version(),
        'sponsor_match': sponsor_match,
    })
    return payload


def build_pr_url(payload: Dict) -> Tuple[str, str, bool]:
    """Build the prefilled GitHub PR URL for this payload.

    Returns (url, filename, too_large). When `too_large` is True the URL is
    still returned but it should NOT be opened — the caller should offer the
    JSON file as a download instead.
    """
    sponsor_slug = _slugify(payload.get('sponsor') or 'sponsor')
    short_uuid = payload['community_id'].split('-')[0]
    filename = f'{sponsor_slug}-{short_uuid}.json'
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    encoded = urllib.parse.quote(body, safe='')
    url = PR_URL_TEMPLATE.format(
        repo=GITHUB_REPO,
        filename=urllib.parse.quote(filename, safe=''),
        value=encoded,
    )
    too_large = len(url.encode('utf-8')) > URL_LENGTH_LIMIT_BYTES
    return url, filename, too_large


def run_export_pipeline(pattern_id: int, db) -> Dict:
    """End-to-end: load pattern + sponsors, run pipeline, return result dict.

    Result dict shape:
      {
        'payload': <dict>,
        'filename': '<slug>-<short>.json',
        'pr_url': '<github url>',
        'too_large': bool,
        'sponsor_match': 'exact'|'alias'|'fuzzy'|'unknown',
      }

    Raises ExportError on quality / tag failures (callers convert to 400).
    """
    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        raise ExportError([f'pattern {pattern_id} not found'])
    if (pattern.get('source') or 'local') != 'local':
        raise ExportError([f"pattern source is '{pattern.get('source')}', only 'local' can be submitted"])

    sponsors = db.get_known_sponsors(active_only=False)
    payload = build_export_payload(pattern, sponsors)
    url, filename, too_large = build_pr_url(payload)
    return {
        'payload': payload,
        'filename': filename,
        'pr_url': url,
        'too_large': too_large,
        'sponsor_match': payload['sponsor_match'],
    }
