"""podping_hosts: upsert accumulation and the 30-day active window."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_hosts_test_')

from database import Database
from database import podping_hosts
from utils.time import ISO_FORMAT


@pytest.fixture
def db():
    d = Database()
    conn = d.get_connection()
    conn.execute("DELETE FROM podping_hosts")
    conn.commit()
    return d


def _iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(ISO_FORMAT)


def test_record_inserts_new_domains(db):
    db.record_podping_hosts({'feeds.megaphone.fm': 3, 'anchor.fm': 1})
    rows = {r['domain']: r for r in db.get_podping_hosts()}
    assert rows['feeds.megaphone.fm']['ping_count'] == 3
    assert rows['anchor.fm']['ping_count'] == 1


def test_record_accumulates_counts_and_moves_last_seen(db):
    db.record_podping_hosts({'anchor.fm': 2})
    first = db.get_podping_hosts()[0]
    db.record_podping_hosts({'anchor.fm': 5})
    row = db.get_podping_hosts()[0]
    assert row['ping_count'] == 7
    assert row['first_seen_at'] == first['first_seen_at']
    assert row['last_seen_at'] >= first['last_seen_at']


def test_record_ignores_empty_input(db):
    db.record_podping_hosts({})
    assert db.get_podping_hosts() == []


def test_active_domains_excludes_stale(db):
    db.record_podping_hosts({'fresh.example': 1, 'stale.example': 1})
    conn = db.get_connection()
    conn.execute(
        "UPDATE podping_hosts SET last_seen_at = ? WHERE domain = 'stale.example'",
        (_iso_days_ago(31),))
    conn.commit()
    assert db.get_active_podping_domains(days=30) == {'fresh.example'}


def test_active_domains_includes_edge_of_window(db):
    db.record_podping_hosts({'edge.example': 1})
    conn = db.get_connection()
    conn.execute(
        "UPDATE podping_hosts SET last_seen_at = ? WHERE domain = 'edge.example'",
        (_iso_days_ago(29),))
    conn.commit()
    assert 'edge.example' in db.get_active_podping_domains(days=30)


def test_hosts_table_is_pruned_past_the_row_cap(db, monkeypatch):
    """Any Hive account can post arbitrary IRIs, so the table needs a bound."""
    monkeypatch.setattr(podping_hosts, 'PODPING_HOSTS_MAX_ROWS', 50)

    db.record_podping_hosts({f'h{i}.example.com': 1 for i in range(60)})

    assert db.count_podping_hosts() == 50


def test_pruning_keeps_the_most_recently_seen_domains(db, monkeypatch):
    monkeypatch.setattr(podping_hosts, 'PODPING_HOSTS_MAX_ROWS', 2)
    db.record_podping_hosts({'old.example.com': 1})
    db.get_connection().execute(
        "UPDATE podping_hosts SET last_seen_at = ? WHERE domain = ?",
        (_iso_days_ago(40), 'old.example.com'))
    db.get_connection().commit()

    db.record_podping_hosts({'new1.example.com': 1, 'new2.example.com': 1})

    assert {r['domain'] for r in db.get_podping_hosts()} == {
        'new1.example.com', 'new2.example.com'}


def test_an_oversized_flush_is_trimmed_to_the_busiest_domains(db, monkeypatch):
    monkeypatch.setattr(podping_hosts, 'PODPING_HOSTS_FLUSH_MAX_DOMAINS', 5)

    db.record_podping_hosts({f'h{i}.example.com': i for i in range(10)})

    kept = {r['domain'] for r in db.get_podping_hosts()}
    assert kept == {f'h{i}.example.com' for i in range(5, 10)}


def test_single_domain_active_lookup(db):
    db.record_podping_hosts({'a.example.com': 1})

    assert db.is_podping_domain_active('a.example.com') is True
    assert db.is_podping_domain_active('b.example.com') is False


def test_a_domain_outside_the_window_is_not_active(db):
    db.record_podping_hosts({'a.example.com': 1})
    db.get_connection().execute(
        "UPDATE podping_hosts SET last_seen_at = ?", (_iso_days_ago(40),))
    db.get_connection().commit()

    assert db.is_podping_domain_active('a.example.com') is False
