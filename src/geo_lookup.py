"""ipwho.is (HTTPS, keyless) geolocation for public client IPs."""
import ipaddress
import logging

import requests

logger = logging.getLogger('podcast.geo')

LOOKUP_URL = 'https://ipwho.is/{ip}?fields=success,country,region,city'
LOOKUPS_PER_TICK = 25
TIMEOUT_SECONDS = 5


def is_private(ip: str) -> bool:
    """True for addresses that must never be sent to a third party."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if getattr(addr, 'ipv4_mapped', None):
        addr = addr.ipv4_mapped
    return not addr.is_global


def lookup_ips(ips: list[str]) -> dict[str, dict]:
    """One GET per IP. The first transport error or 429 stops the batch; the rest stay pending."""
    result = {}
    for ip in ips[:LOOKUPS_PER_TICK]:
        try:
            response = requests.get(LOOKUP_URL.format(ip=ip), timeout=TIMEOUT_SECONDS)
            if 400 <= response.status_code < 500 and response.status_code != 429:
                result[ip] = {'status': 'failed'}
                continue
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            logger.warning(f"Geo lookup failed, stopping this tick: {exc}")
            break
        if body.get('success'):
            result[ip] = {'status': 'ok', 'country': body.get('country'),
                          'region': body.get('region'), 'city': body.get('city')}
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
    public = public[:LOOKUPS_PER_TICK]
    found = lookup_ips(public)
    for ip in public:
        entry = found.get(ip)
        if entry is None:
            continue
        if entry['status'] == 'ok':
            db.store_geo(ip, 'ok', entry['country'], entry['region'], entry['city'])
        else:
            db.store_geo(ip, 'failed')
