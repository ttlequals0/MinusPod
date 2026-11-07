"""Turn a live podcast.db into a scrubbed demo snapshot."""
# ruff: noqa: S608 (SQL identifiers come from PRAGMA and the allowlist, quoted by q())
import argparse
import json
import os
import sqlite3
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from scripts.demo.export_allowlist import (
    DEMO_SETTINGS_ALLOW,
    DROPPED_TABLE_PREFIXES,
    EPISODE_REF,
    EXPORT_TABLES,
    PODCAST_REF,
    SETTINGS_FORCE,
    SETTINGS_FORCE_UNREGISTERED,
)
from scripts.demo.leak_check import Hit, check_sqlite, report_hits
from scripts.demo.scrub import (
    map_ip,
    replace_emails,
    replace_hosts,
    replace_paths,
    scrub_source_url,
    shift_iso,
    shift_iso_in_text,
)

# table -> timestamp column, for log tables trimmed to the window
LOG_TIME_COLUMNS = {
    'llm_call_usage': 'created_at',
    'ad_reviewer_log': 'created_at',
    'addressing_log': 'created_at',
    'cue_detections': 'created_at',
    'systemone_call_diagnostics': 'created_at',
}

JSON_TIME_COLUMNS = (
    'p20_channel_json', 'p20_item_json', 'processing_stats_json',
    'ad_markers_json', 'payload', 'before', 'applied',
)

ISO_GLOB = '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
ISO_TS_GLOB = '*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*'
EXCLUSIONS_PATH = Path(__file__).with_name('exclusions.json')


@dataclass
class SnapshotReport:
    feeds_kept: int = 0
    feeds_excluded: int = 0
    episodes_kept: int = 0
    episodes_dropped: int = 0
    rows_per_table: dict[str, int] = field(default_factory=dict)
    unclassified_tables: list[str] = field(default_factory=list)
    delta_days: int = 0
    output_bytes: int = 0
    leaks: list[Hit] = field(default_factory=list)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    return {r[0] for r in rows}


def table_info(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f'PRAGMA table_info({q(table)})').fetchall()


def text_columns(conn: sqlite3.Connection, table: str, only: list[str]) -> list[str]:
    return [
        r[1] for r in table_info(conn, table)
        if r[1] in only and any(t in r[2].upper() for t in ('CHAR', 'CLOB', 'TEXT', 'TIMESTAMP', 'DATE'))
    ]


def copy_source(source: Path, work: Path) -> sqlite3.Connection:
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    conn = sqlite3.connect(work)
    src.backup(conn)
    src.close()
    conn.execute('PRAGMA journal_mode = DELETE')
    conn.execute('PRAGMA foreign_keys = OFF')
    conn.execute('PRAGMA secure_delete = ON')
    return conn


def drop_search_index(conn: sqlite3.Connection) -> None:
    by_name = ' OR '.join(f"name GLOB '{p}*'" for p in DROPPED_TABLE_PREFIXES)
    by_sql = ' OR '.join(f"sql GLOB '*{p}*'" for p in DROPPED_TABLE_PREFIXES)
    # triggers on other tables that feed the index would break later writes
    for (name,) in conn.execute(f"SELECT name FROM sqlite_master WHERE type = 'trigger' AND ({by_sql})").fetchall():
        conn.execute(f'DROP TRIGGER IF EXISTS {q(name)}')
    # dropping a virtual table removes its shadow tables
    for (name,) in conn.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'table' AND ({by_name}) AND sql GLOB 'CREATE VIRTUAL*'"
    ).fetchall():
        conn.execute(f'DROP TABLE IF EXISTS {q(name)}')
    for (name,) in conn.execute(f"SELECT name FROM sqlite_master WHERE type = 'table' AND ({by_name})").fetchall():
        conn.execute(f'DROP TABLE IF EXISTS {q(name)}')
    conn.commit()


def drop_episodes(conn: sqlite3.Connection, where: str, params: tuple = ()) -> None:
    conn.execute('DROP TABLE IF EXISTS temp._drop_ep')
    conn.execute(
        'CREATE TEMP TABLE _drop_ep AS SELECT e.id AS id, e.podcast_id AS pid, e.episode_id AS eid, '
        f'(SELECT slug FROM podcasts p WHERE p.id = e.podcast_id) AS slug FROM episodes e WHERE {where}', params,
    )
    conn.execute('CREATE INDEX temp._drop_ep_pid ON _drop_ep (pid, eid)')
    conn.execute('CREATE INDEX temp._drop_ep_slug ON _drop_ep (slug, eid)')
    have = user_tables(conn)
    pairs = {'id': 'pid', 'slug': 'slug'}
    for table, (pcol, ecol, kind) in EPISODE_REF.items():
        if table not in have:
            continue
        if pcol:
            conn.execute(
                f'DELETE FROM {q(table)} WHERE ({q(pcol)}, {q(ecol)}) IN (SELECT {pairs[kind]}, eid FROM temp._drop_ep)'
            )
        else:
            conn.execute(f'DELETE FROM {q(table)} WHERE {q(ecol)} IN (SELECT id FROM temp._drop_ep)')
    conn.execute('DELETE FROM episodes WHERE id IN (SELECT id FROM temp._drop_ep)')
    conn.execute('DROP TABLE temp._drop_ep')


def exclude_feeds(conn: sqlite3.Connection, excluded_slugs: set[str]) -> int:
    if not excluded_slugs:
        return 0
    marks = ','.join('?' * len(excluded_slugs))
    slugs = sorted(excluded_slugs)
    ids = [r[0] for r in conn.execute(f'SELECT id FROM podcasts WHERE slug IN ({marks})', slugs)]
    id_marks = ','.join('?' * len(ids))
    slug_marks = marks
    if ids:
        drop_episodes(conn, f'podcast_id IN ({id_marks})', tuple(ids))
    have = user_tables(conn)
    # mirrors delete_podcast: podcast-scoped patterns go with their dependants,
    # wider-scope patterns stay but stop naming the feed
    doomed = f"SELECT id FROM ad_patterns WHERE scope = 'podcast' AND podcast_id IN ({slug_marks})"
    for table in ('audio_fingerprints', 'pattern_corrections'):
        conn.execute(f'DELETE FROM {table} WHERE pattern_id IN ({doomed})', slugs)
    conn.execute(f"DELETE FROM ad_patterns WHERE scope = 'podcast' AND podcast_id IN ({slug_marks})", slugs)
    conn.execute(f'UPDATE ad_patterns SET podcast_id = NULL WHERE podcast_id IN ({slug_marks})', slugs)
    for table, (col, kind) in PODCAST_REF.items():
        if table not in have or table == 'ad_patterns':
            continue
        keys, mk = (slugs, slug_marks) if kind == 'slug' else (ids, id_marks)
        if not keys:
            continue
        conn.execute(f'DELETE FROM {q(table)} WHERE {q(col)} IN ({mk})', keys)
    if ids:
        conn.execute(f'DELETE FROM podcasts WHERE id IN ({id_marks})', ids)
    # cascades do not fire with foreign keys off
    for table in ('audio_fingerprints', 'pattern_cleanup_suggestions'):
        if table in have:
            conn.execute(f'DELETE FROM {q(table)} WHERE pattern_id NOT IN (SELECT id FROM ad_patterns)')
    conn.commit()
    return len(ids)


def apply_window(conn: sqlite3.Connection, cutoff: str) -> None:
    drop_episodes(conn, 'COALESCE(published_at, created_at) < ?', (cutoff,))
    have = user_tables(conn)
    conn.execute('DELETE FROM processing_history WHERE processed_at < ?', (cutoff,))
    conn.execute('DELETE FROM episode_downloads WHERE last_seen_at < ?', (cutoff,))
    for table, col in LOG_TIME_COLUMNS.items():
        if table in have:
            conn.execute(f'DELETE FROM {q(table)} WHERE {q(col)} < ?', (cutoff,))
    conn.execute('DELETE FROM ip_geo_cache WHERE ip NOT IN (SELECT client_ip FROM episode_downloads)')
    conn.commit()


def blank_value(col: sqlite3.Row) -> str:
    if not col[3]:
        return 'NULL'
    if col[4] is not None:
        return col[4]
    decl = col[2].upper()
    if 'BLOB' in decl:
        return "x''"
    if any(t in decl for t in ('CHAR', 'CLOB', 'TEXT')):
        return "''"
    return '0'


def apply_allowlist(conn: sqlite3.Connection) -> list[str]:
    have = user_tables(conn)
    for table in EXPORT_TABLES:
        kept = EXPORT_TABLES[table]
        if table not in have:
            continue
        if not kept:
            conn.execute(f'DELETE FROM {q(table)}')
            continue
        sets = [
            f'{q(c[1])} = {blank_value(c)}' for c in table_info(conn, table)
            if c[1] not in kept and not c[5]
        ]
        if sets:
            conn.execute(f'UPDATE {q(table)} SET {", ".join(sets)}')
    # tables the allowlist does not know about are emptied, never exported
    unclassified = sorted(have - set(EXPORT_TABLES))
    for table in unclassified:
        conn.execute(f'DELETE FROM {q(table)}')
    marks = ','.join('?' * len(DEMO_SETTINGS_ALLOW))
    conn.execute(f'DELETE FROM settings WHERE key NOT IN ({marks})', sorted(DEMO_SETTINGS_ALLOW))
    forced = {**SETTINGS_FORCE, **SETTINGS_FORCE_UNREGISTERED}
    conn.executemany(
        'INSERT INTO settings (key, value, is_default) VALUES (?, ?, 0) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value, is_default = 0',
        list(forced.items()),
    )
    conn.commit()
    return unclassified


def scrub(conn: sqlite3.Connection, hosts: list[str], salt: bytes) -> None:
    rows = conn.execute('SELECT id, slug, source_url FROM podcasts').fetchall()
    conn.executemany(
        'UPDATE podcasts SET source_url = ? WHERE id = ?',
        [(new, i) for i, slug, url in rows if (new := scrub_source_url(url, slug)) != url],
    )
    ips = [r[0] for r in conn.execute('SELECT DISTINCT client_ip FROM episode_downloads')]
    conn.executemany(
        'UPDATE episode_downloads SET client_ip = ? WHERE client_ip = ?',
        [(new, ip) for ip in ips if (new := map_ip(ip, salt)) != ip],
    )
    conn.execute('UPDATE episode_downloads SET last_request_line = NULL, last_request_headers = NULL')
    geo = conn.execute('SELECT ip, country, looked_up_at, status FROM ip_geo_cache ORDER BY rowid').fetchall()
    conn.execute('DELETE FROM ip_geo_cache')
    conn.executemany(
        'INSERT OR IGNORE INTO ip_geo_cache (ip, country, region, city, looked_up_at, status) VALUES (?, ?, NULL, NULL, ?, ?)',
        [(map_ip(ip, salt), country, at, status) for ip, country, at, status in geo],
    )
    # secret_hash has CHECK(length = 64), so '' is not allowed
    conn.execute("UPDATE feed_subscriber_keys SET secret_hash = ?, label = 'Subscriber'", ('0' * 64,))
    if hosts:
        replace_in_text_columns(conn, hosts)
    rewrite_text_columns(conn, 'replace_emails', replace_emails, lambda c: f"{c} LIKE '%@%'")
    rewrite_text_columns(conn, 'replace_paths', replace_paths, lambda c: PATH_FILTER.format(c=c))
    conn.commit()


PATH_FILTER = ' OR '.join(
    "{c} LIKE '%" + p + "%'" for p in ('/app/data', '/home/', '/Users/', '/mnt', '/srv', '/var/lib')
)


def rewrite_text_columns(conn: sqlite3.Connection, name: str, fn, cond, params: list[str] = ()) -> None:
    conn.create_function(name, 1, lambda v: fn(v) if isinstance(v, str) else v, deterministic=True)
    for table in EXPORT_TABLES:
        if table not in user_tables(conn):
            continue
        for col in text_columns(conn, table, EXPORT_TABLES[table]):
            c = q(col)
            conn.execute(
                f"UPDATE {q(table)} SET {c} = {name}({c}) "
                f"WHERE typeof({c}) = 'text' AND ({cond(c)}) AND {name}({c}) IS NOT {c}",
                params,
            )


def replace_in_text_columns(conn: sqlite3.Connection, hosts: list[str]) -> None:
    rewrite_text_columns(
        conn, 'replace_hosts', lambda v: replace_hosts(v, hosts),
        lambda c: ' OR '.join(f'LOWER({c}) LIKE ?' for _ in hosts), [f'%{h.lower()}%' for h in hosts],
    )


def compute_delta(conn: sqlite3.Connection, export_day: date) -> int:
    newest = []
    for table, col in (('episodes', 'created_at'), ('processing_history', 'processed_at'), ('episode_downloads', 'last_seen_at')):
        newest.append(conn.execute(f'SELECT MAX(SUBSTR({col}, 1, 10)) FROM {table} WHERE {col} GLOB ?', (ISO_GLOB,)).fetchone()[0])
    newest = [n for n in newest if n]
    if not newest:
        return 0
    return (export_day - date.fromisoformat(max(newest))).days


def shift_timestamps(conn: sqlite3.Connection, delta_days: int) -> None:
    if delta_days == 0:
        return
    for name, fn in (('shift_iso', shift_iso), ('shift_text', shift_iso_in_text)):
        conn.create_function(
            name, 2, lambda v, d, fn=fn: fn(v, d) if isinstance(v, str) else v, deterministic=True,
        )
    for table in EXPORT_TABLES:
        if table not in user_tables(conn):
            continue
        for col in text_columns(conn, table, EXPORT_TABLES[table]):
            fn, glob = ('shift_text', ISO_TS_GLOB) if col in JSON_TIME_COLUMNS else ('shift_iso', ISO_GLOB)
            conn.execute(
                f'UPDATE {q(table)} SET {q(col)} = {fn}({q(col)}, ?) '
                f"WHERE typeof({q(col)}) = 'text' AND {q(col)} GLOB ? AND {fn}({q(col)}, ?) IS NOT {q(col)}",
                (delta_days, glob, delta_days),
            )
    conn.commit()


def check_foreign_keys(conn: sqlite3.Connection) -> None:
    bad = conn.execute('PRAGMA foreign_key_check').fetchall()
    if bad:
        rows = ', '.join(f'({r[0]}, rowid {r[1]}, parent {r[2]})' for r in bad[:5])
        raise RuntimeError(f'{len(bad)} foreign key violations after export: {rows}')


def snapshot(
    source: Path, out: Path, *, days: int, excluded_slugs: set[str], hosts: list[str],
    export_day: date, salt: bytes,
) -> SnapshotReport:
    report = SnapshotReport()
    if out.resolve() == source.resolve():
        raise ValueError('--out must differ from --source')
    out.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(dir=out.parent, prefix='.snapshot-'))
    try:
        conn = copy_source(source, work_dir / 'work.db')
        try:
            drop_search_index(conn)
            episodes_before = conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0]
            report.feeds_excluded = exclude_feeds(conn, excluded_slugs)
            apply_window(conn, (export_day - timedelta(days=days)).isoformat())
            report.unclassified_tables = apply_allowlist(conn)
            scrub(conn, hosts, salt)
            report.delta_days = compute_delta(conn, export_day)
            shift_timestamps(conn, report.delta_days)
            report.feeds_kept = conn.execute('SELECT COUNT(*) FROM podcasts').fetchone()[0]
            report.episodes_kept = conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0]
            report.episodes_dropped = episodes_before - report.episodes_kept
            for table in sorted(user_tables(conn)):
                report.rows_per_table[table] = conn.execute(f'SELECT COUNT(*) FROM {q(table)}').fetchone()[0]
            check_foreign_keys(conn)
            out.unlink(missing_ok=True)
            conn.execute('VACUUM INTO ?', (str(out),))
        finally:
            conn.close()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    report.leaks = check_sqlite(out, hosts)
    report.output_bytes = out.stat().st_size
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--exclusions', type=Path, default=EXCLUSIONS_PATH)
    parser.add_argument('--hosts', default='')
    parser.add_argument('--export-day', type=date.fromisoformat, default=None)
    parser.add_argument('--report-unclassified', action='store_true')
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 1
    if not args.source.is_file():
        print(f'source not found: {args.source}', file=sys.stderr)
        return 1
    if args.days < 1:
        print('--days must be at least 1', file=sys.stderr)
        return 1
    if args.out.resolve() == args.source.resolve():
        print('--out must differ from --source', file=sys.stderr)
        return 1
    try:
        data = json.loads(args.exclusions.read_text())
    except (OSError, ValueError) as e:
        print(f'cannot read exclusions: {e}', file=sys.stderr)
        return 1
    slugs = data.get('slugs') if isinstance(data, dict) else None
    if not isinstance(slugs, list) or not all(isinstance(x, str) for x in slugs):
        print('exclusions must be an object with "slugs" as a list of strings', file=sys.stderr)
        return 1
    slugs = set(slugs)
    hosts = [h.strip() for h in args.hosts.split(',') if h.strip()]
    report = snapshot(
        args.source, args.out, days=args.days, excluded_slugs=slugs, hosts=hosts,
        export_day=args.export_day or date.today(), salt=os.urandom(32),
    )
    if report.leaks:
        print(f'LEAK CHECK FAILED: {len(report.leaks)} hits in {args.out}', file=sys.stderr)
        report_hits(report.leaks)
        args.out.unlink(missing_ok=True)
        print(f'removed {args.out}', file=sys.stderr)
        return 2
    print(f'feeds kept      | {report.feeds_kept}')
    print(f'feeds excluded  | {report.feeds_excluded}')
    print(f'episodes kept   | {report.episodes_kept}')
    print(f'episodes dropped| {report.episodes_dropped}')
    print(f'delta_days      | {report.delta_days}')
    print(f'output bytes    | {report.output_bytes}')
    for table, n in report.rows_per_table.items():
        print(f'rows {table} | {n}')
    if args.report_unclassified:
        print('unclassified tables (emptied): ' + (', '.join(report.unclassified_tables) or 'none'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
