"""Leaf module for the cleanup review hash; no project deps, safe to import from database/."""
import hashlib
import json

# Stamp set once a pattern's review is unusable INVALID_LIMIT times; parks it until forced.
INVALID_MARKER = 'invalid'


def review_hash(text: str | None, sponsor: str | None) -> str:
    """Hash of normalized text plus sponsor; a match means already reviewed."""
    norm = ' '.join((text or '').lower().split())
    raw = f"{norm}\x1f{(sponsor or '').strip().lower()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def stats_evidence_hash(kind: str, text: str | None, sponsor: str | None,
                        evidence: dict) -> str:
    norm_text = ' '.join((text or '').lower().split())
    norm_sponsor = ' '.join((sponsor or '').lower().split())
    raw = json.dumps([kind, norm_text, norm_sponsor, evidence], sort_keys=True,
                     separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
