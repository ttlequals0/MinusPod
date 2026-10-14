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
                result[ip] = {'status': 'unresolvable'}
                continue
            response.raise_for_status()
        except Exception as exc:
            logger.warning(f"Geo lookup failed, stopping this tick: {exc}")
            break
        try:
            body = response.json()
        except ValueError:
            result[ip] = {'status': 'failed'}
            continue
        if body.get('success'):
            result[ip] = {'status': 'ok', 'country': body.get('country'),
                          'region': body.get('region'), 'city': body.get('city')}
        else:
            result[ip] = {'status': 'unresolvable'}
    return result


def run_geo_lookup(db) -> None:
    """Resolve pending download IPs when geo lookup is enabled."""
    if not db.get_setting_bool('download_geo_lookup_enabled', False):
        return
    rows, public = [], []
    for ip in db.pending_geo_ips():
        if is_private(ip):
            rows.append((ip, 'private', None, None, None))
        else:
            public.append(ip)
    for ip, entry in (lookup_ips(public) if public else {}).items():
        rows.append((ip, entry['status'], entry.get('country'),
                     entry.get('region'), entry.get('city')))
    if rows:
        db.store_geo_many(rows)
