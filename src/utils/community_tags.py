"""Tag vocabulary, sponsor seed, iTunes-category map, PII constants for community patterns."""
from __future__ import annotations

import csv
import json
import os
import re
from functools import lru_cache
from typing import Dict, FrozenSet, List, Tuple

_SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'seed_data')

UNIVERSAL_TAG = 'universal'

# Single source of truth for the upstream MinusPod repo identity. Used by
# both the export pipeline's prefilled-PR URL builder and the sync job's
# manifest fetch URL.
GITHUB_REPO = 'ttlequals0/MinusPod'
COMMUNITY_MANIFEST_URL = (
    f'https://raw.githubusercontent.com/{GITHUB_REPO}/main/patterns/community/index.json'
)

# Schema versions. MANIFEST_VERSION bumps when the manifest envelope shape
# changes; VOCABULARY_VERSION bumps when the tag list is added to / removed
# from. Both ship with the app image; this module is the single owner — the
# manifest generator and the sync job both import these constants, not their
# own copies.
MANIFEST_VERSION = 1
VOCABULARY_VERSION = 1

# Community submission bundle: format string + version, used by the export
# builder (community_export.build_bundle), the PR-side validator, and the
# manifest generator. Single source of truth so the three modules can't
# drift on the spelling.
BUNDLE_FORMAT = 'minuspod-community-submission'
BUNDLE_VERSION = 1


def iter_bundle_patterns(raw):
    """Yield each pattern dict inside a payload, regardless of shape.

    A flat per-pattern file yields ``raw`` itself. A bundle file (``raw['format']
    == BUNDLE_FORMAT``) yields each entry in ``raw['patterns']``. Non-dict
    entries are skipped. Callers add their own indexing or filtering.
    """
    if not isinstance(raw, dict):
        return
    if raw.get('format') == BUNDLE_FORMAT:
        for p in raw.get('patterns') or []:
            if isinstance(p, dict):
                yield p
        return
    yield raw

# ad_patterns.source values. Centralized so API/DB/UI agree on spelling.
PATTERN_SOURCE_LOCAL = 'local'
PATTERN_SOURCE_COMMUNITY = 'community'
PATTERN_SOURCE_IMPORTED = 'imported'
PATTERN_SOURCES: FrozenSet[str] = frozenset({
    PATTERN_SOURCE_LOCAL, PATTERN_SOURCE_COMMUNITY, PATTERN_SOURCE_IMPORTED,
})


@lru_cache(maxsize=1)
def _vocabulary_tags() -> FrozenSet[str]:
    """Tags from src/seed_data/tag_vocabulary.csv (no 'universal' — see VALID_TAGS)."""
    path = os.path.join(_SEED_DIR, 'tag_vocabulary.csv')
    tags = set()
    with open(path, 'r', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tag = (row.get('tag') or '').strip()
            if tag:
                tags.add(tag)
    return frozenset(tags)


@lru_cache(maxsize=1)
def valid_tags() -> FrozenSet[str]:
    """All accepted tags: vocabulary CSV plus the special 'universal' opt-in."""
    return frozenset(_vocabulary_tags() | {UNIVERSAL_TAG})


@lru_cache(maxsize=1)
def vocabulary_payload() -> Dict[str, object]:
    """Categorized vocabulary view used by patterns/vocabulary.json AND the
    /api/v1/tags/vocabulary endpoint. Cached because the source CSV ships
    with the app image and never changes at runtime.
    """
    path = os.path.join(_SEED_DIR, 'tag_vocabulary.csv')
    genres: List[Dict[str, str]] = []
    industries: List[Dict[str, str]] = []
    with open(path, 'r', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            entry = {'tag': row['tag'], 'description': row['description']}
            if row['category'] == 'podcast_genre':
                genres.append(entry)
            elif row['category'] == 'sponsor_industry':
                industries.append(entry)
    return {
        'vocabulary_version': VOCABULARY_VERSION,
        'all_tags': sorted(valid_tags()),
        'podcast_genres': genres,
        'sponsor_industries': industries,
        'special_tags': [{
            'tag': UNIVERSAL_TAG,
            'description': 'Sponsor advertises broadly across all podcast genres.',
        }],
    }


@lru_cache(maxsize=1)
def itunes_category_map() -> Dict[str, str]:
    """iTunes category string -> vocabulary tag (case-insensitive lookup via .lower()).

    Keys in the file are the canonical iTunes labels (e.g. 'Health & Fitness').
    We expose a lowercased view for lookup.
    """
    path = os.path.join(_SEED_DIR, 'itunes_category_map.json')
    with open(path, 'r', encoding='utf-8') as fh:
        raw = json.load(fh)
    return {k.lower(): v for k, v in raw.items() if not k.startswith('_')}


def map_itunes_category(category: str) -> str | None:
    """Look up a single iTunes category string and return the vocab tag, or None."""
    if not category:
        return None
    return itunes_category_map().get(category.strip().lower())


@lru_cache(maxsize=1)
def sponsor_seed() -> List[Dict[str, object]]:
    """List of {name, aliases: List[str], tags: List[str]} from sponsors_final.csv.

    Names and tags are preserved verbatim. Aliases and tags are pipe-delimited in the CSV.
    """
    path = os.path.join(_SEED_DIR, 'sponsors_final.csv')
    rows: List[Dict[str, object]] = []
    with open(path, 'r', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get('name') or '').strip()
            if not name:
                continue
            aliases_raw = (row.get('aliases') or '').strip()
            tags_raw = (row.get('tags') or '').strip()
            aliases = [a.strip() for a in aliases_raw.split('|') if a.strip()] if aliases_raw else []
            tags = [t.strip() for t in tags_raw.split('|') if t.strip()] if tags_raw else []
            rows.append({'name': name, 'aliases': aliases, 'tags': tags})
    return rows


# Consumer email domains — strip on export (best-effort, tunable).
CONSUMER_EMAIL_DOMAINS: FrozenSet[str] = frozenset({
    'gmail.com', 'yahoo.com', 'aol.com', 'hotmail.com', 'outlook.com',
    'icloud.com', 'me.com', 'mac.com', 'protonmail.com', 'proton.me',
    'mail.com', 'gmx.com', 'gmx.net', 'yandex.com', 'yandex.ru',
    'qq.com', '163.com', 'live.com', 'msn.com', 'hey.com', 'fastmail.com',
    'tutanota.com',
})

# Toll-free prefixes — phone numbers using these are KEPT in export text.
# Everything else matched by the phone regex is stripped.
TOLLFREE_PREFIXES_NANP: Tuple[str, ...] = ('800', '833', '844', '855', '866', '877', '888')
TOLLFREE_PREFIXES_UK: Tuple[str, ...] = ('0800', '0808')
TOLLFREE_PREFIX_AU: str = '1800'
TOLLFREE_PREFIX_UIFN: str = '+800'


# Conservative phone regex: must have at least one dash/paren/dot/space separator
# or a country code prefix. Bare 7-10 digit runs (likely codes, dates, etc.) are
# left alone. Captures the leading dialable prefix to classify toll-free.
PHONE_REGEX = re.compile(
    r'''(?x)
    (?<![\d.])                                  # not preceded by digit/dot
    (
        (?:\+?\d{1,3}[-.\s])?                   # optional country code
        (?:\(?(\d{3,4})\)?[-.\s])               # area/prefix with separator
        \d{3}[-.\s]?\d{3,4}                     # local
    )
    (?!\d)                                      # not followed by digit
    ''',
)

EMAIL_REGEX = re.compile(r'\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')

# Canonicalization stopwords for dedupe (token-bounded only).
CANONICAL_STOPWORDS: FrozenSet[str] = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'to', 'of', 'for', 'by',
    'and', 'or', 'but', 'in', 'on', 'at', 'with', 'from', 'this', 'that',
    'you', 'your', 'we', 'our', 'my', 'me', 'it', 'its', 'as',
})

CANONICAL_DAYS: FrozenSet[str] = frozenset({
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
})

CANONICAL_MONTHS: FrozenSet[str] = frozenset({
    'january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december',
    'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept',
    'oct', 'nov', 'dec',
})

CANONICAL_RELATIVE_TIME: FrozenSet[str] = frozenset({
    'today', 'tomorrow', 'yesterday', 'tonight', 'weekend', 'weekday',
})

YEAR_REGEX = re.compile(r'\b(19|20)\d{2}\b')
DATE_REGEX = re.compile(r'\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b')


def is_tollfree(phone_match: str) -> bool:
    """Return True if the captured phone string starts with a toll-free prefix."""
    digits_only = re.sub(r'[^\d+]', '', phone_match)
    # Strip optional leading +1 for NANP
    if digits_only.startswith('+1'):
        digits_only = digits_only[2:]
    elif digits_only.startswith('1') and len(digits_only) > 10:
        digits_only = digits_only[1:]
    if digits_only.startswith(TOLLFREE_PREFIXES_NANP):
        return True
    if digits_only.startswith(TOLLFREE_PREFIXES_UK):
        return True
    if digits_only.startswith(TOLLFREE_PREFIX_AU):
        return True
    if phone_match.startswith(TOLLFREE_PREFIX_UIFN):
        return True
    return False
