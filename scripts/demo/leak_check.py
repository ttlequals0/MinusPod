"""Scan files and SQLite cells for secrets, private hosts, paths and public IPs."""
import argparse
import ipaddress
import re
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DOC_NETS = [ipaddress.ip_network(n) for n in (
    '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32',
)]
EMAIL_OK = {'example.com', 'example.org', 'example.net'}
SKIP_SUFFIXES = {
    '.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mp3', '.db', '.zip', '.gz',
    '.woff', '.woff2',
}

# four octets, not part of a longer dotted run or a product token like Chrome/155.0.0.0;
# octet range is checked afterwards
IPV4 = r'(?<![\d.])(?<![A-Za-z]/)\d{1,3}(?:\.\d{1,3}){3}(?!\d|\.\d)'
# colon-hex literal with at least two colons (ipaddress rejects the non-IPs), or ::ffff:a.b.c.d
IPV6 = (
    r'(?<![\w:])::[fF]{4}:\d{1,3}(?:\.\d{1,3}){3}(?![\w:.])'
    r'|(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])'
)
PATH_END = r"""[^\s"'\\\])}>]*"""

PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(rx)) for name, rx in [
        ('anthropic_key', r'sk-ant-[A-Za-z0-9_-]{20,}'),
        ('openai_key', r'(?<![A-Za-z0-9])sk-(?!ant-|or-)(proj-)?[A-Za-z0-9_-]{20,}'),
        ('openrouter_key', r'sk-or-[A-Za-z0-9_-]{20,}'),
        ('encrypted_setting', r"""enc:v1:[^\s"']+"""),
        ('bearer', r'(?i)bearer\s+[A-Za-z0-9._-]{20,}'),
        ('jwt', r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+'),
        ('abs_path', r'(?m)(^|[^A-Za-z0-9_./-])(/app/data|/home/[A-Za-z]|/Users/[A-Za-z]|/mnt/|/srv/|/var/lib/)' + PATH_END),
        ('email', r'(?i)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}'),
        ('private_feed_host', r"""(?i)patreon\.com/rss|(?:supercast\.com|supportingcast\.fm|memberful\.com)/[^\s"'<>]*(?:rss|feed|[A-Za-z0-9_-]{20,})"""),
        ('feed_key', r'[?&]key=[A-Za-z0-9_-]{16,}'),
        ('public_ip', f'{IPV4}|{IPV6}'),
    ]
]


@dataclass
class Hit:
    source: str
    pattern: str
    excerpt: str


def host_pattern(host: str) -> tuple[str, re.Pattern]:
    # whole-token match; a subdomain of the host still counts
    rx = rf'(?i)(?<![A-Za-z0-9-]){re.escape(host)}(?![A-Za-z0-9-]|\.[A-Za-z0-9])'
    return f'host:{host}', re.compile(rx)


def compile_patterns(hosts: list[str]) -> list[tuple[str, re.Pattern]]:
    return PATTERNS + [host_pattern(h) for h in hosts]


def accepted(name: str, text: str) -> bool:
    if name == 'email':
        return text.rsplit('@', 1)[1].lower() not in EMAIL_OK
    if name != 'public_ip':
        return True
    try:
        if ':' not in text:
            octets = [int(p) for p in text.split('.')]
            if octets[0] == 0 or max(octets) > 255:
                return False
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not any(ip.version == n.version and ip in n for n in DOC_NETS)


def excerpt(text: str, m: re.Match) -> str:
    return text[max(0, m.start() - 20):m.start()] + m.group(0)[:4] + '...' + text[m.end():m.end() + 20]


def check_text(
    text: str, source: str, hosts: list[str], patterns: list[tuple[str, re.Pattern]] | None = None,
) -> list[Hit]:
    found = []
    for name, rx in patterns or compile_patterns(hosts):
        for m in rx.finditer(text):
            if accepted(name, m.group(0)):
                found.append(Hit(source, name, excerpt(text, m)))
    return found


def check_files(paths: Iterable[Path], hosts: list[str]) -> list[Hit]:
    found = []
    patterns = compile_patterns(hosts)
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file())
        for f in files:
            if f.suffix.lower() in SKIP_SUFFIXES:
                continue
            found += check_text(f.read_text(encoding='utf-8', errors='ignore'), str(f), hosts, patterns)
    return found


def check_sqlite(db: Path, hosts: list[str]) -> list[Hit]:
    found = []
    patterns = compile_patterns(hosts)
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            qt = '"' + table.replace('"', '""') + '"'
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info({qt})')]
            sel = ', '.join('"' + c.replace('"', '""') + '"' for c in cols)
            try:
                cur = conn.execute(f'SELECT rowid, {sel} FROM {qt}')  # noqa: S608
            except sqlite3.OperationalError:  # WITHOUT ROWID
                cur = conn.execute(f'SELECT NULL, {sel} FROM {qt}')  # noqa: S608
            for row in cur:
                for col, val in zip(cols, row[1:], strict=True):
                    if isinstance(val, bytes):
                        val = val.decode('utf-8', errors='ignore')
                        # skip binary blobs such as MFCC arrays
                        if not val or sum(c.isprintable() or c.isspace() for c in val) < 0.9 * len(val):
                            continue
                    if isinstance(val, str):
                        found += check_text(val, f'{table}.{col}#{row[0]}', hosts, patterns)
    finally:
        conn.close()
    return found


def report_hits(hits: list[Hit], limit: int = 20) -> None:
    by_pattern: dict[str, list[Hit]] = {}
    for h in hits:
        by_pattern.setdefault(h.pattern, []).append(h)
    for name, group in sorted(by_pattern.items()):
        print(f'{name} | {len(group)}')
        for h in group[:limit]:
            print(f'  {h.source} | {h.excerpt!r}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path)
    parser.add_argument('--sqlite', type=Path)
    parser.add_argument('--hosts', default='')
    args = parser.parse_args(argv)
    hosts = [h.strip() for h in args.hosts.split(',') if h.strip()]
    targets = [*args.paths, *([args.sqlite] if args.sqlite else [])]
    missing = [p for p in targets if not p.exists()]
    if missing or not targets:
        print('nothing to scan: ' + (', '.join(map(str, missing)) or 'no paths given'), file=sys.stderr)
        return 2
    hits = check_files(args.paths, hosts)
    if args.sqlite:
        hits += check_sqlite(args.sqlite, hosts)
    report_hits(hits)
    return 2 if hits else 0


if __name__ == '__main__':
    sys.exit(main())
