"""ip-api.com batch geolocation for public client IPs."""
import ipaddress
import logging

import requests

logger = logging.getLogger('podcast.geo')

BATCH_URL = 'http://ip-api.com/batch?fields=status,query,country,regionName,city'
BATCH_LIMIT = 100
TIMEOUT_SECONDS = 10


def is_private(ip: str) -> bool:
    """True for addresses that must never be sent to a third party."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def lookup_batch(ips: list[str]) -> dict[str, dict]:
    """Look up up to 100 IPs; returns {} on any error."""
    ips = ips[:BATCH_LIMIT]
    if not ips:
        return {}
    try:
        response = requests.post(BATCH_URL, json=ips, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        entries = response.json()
    except Exception as exc:
        logger.warning(f"Geo lookup failed: {exc}")
        return {}
    result = {}
    for entry in entries:
        ip = entry.get('query')
        if not ip:
            continue
        if entry.get('status') == 'success':
            result[ip] = {'status': 'ok', 'country': entry.get('country'),
                          'region': entry.get('regionName'), 'city': entry.get('city')}
        else:
            result[ip] = {'status': 'failed'}
    return result


def run_geo_lookup(db) -> None:
    """Resolve pending download IPs when geo lookup is enabled."""
    if not db.get_setting_bool('download_geo_lookup_enabled', False):
        return
    public = []
    for ip in db.pending_geo_ips():
        if is_private(ip):
            db.store_geo(ip, 'private')
        else:
            public.append(ip)
    if not public:
        return
    found = lookup_batch(public)
    for ip in public:
        entry = found.get(ip)
        if entry and entry['status'] == 'ok':
            db.store_geo(ip, 'ok', entry['country'], entry['region'], entry['city'])
        else:
            db.store_geo(ip, 'failed')
