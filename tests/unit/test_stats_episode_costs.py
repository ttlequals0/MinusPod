"""Per-episode cost breakdown over the llm_call_usage ledger.

Pins the row shape and ordering the /stats/episode-costs page renders, and
that one page costs one pass over the ledger table.
"""
from config import normalize_model_key


def _seed_price(db, model_id, input_cost, output_cost):
    db.upsert_fetched_pricing([{
        'match_key': normalize_model_key(model_id),
        'raw_model_id': model_id,
        'display_name': model_id,
        'input_cost_per_mtok': input_cost,
        'output_cost_per_mtok': output_cost,
    }], source='litellm')


def _call(db, *, run_id, podcast_id, episode_id, model, provider='anthropic',
          input_tokens=1_000_000, output_tokens=0, phase_key='detection'):
    attempt_id = db.begin_llm_attempt(
        run_id=run_id, podcast_id=podcast_id, episode_id=episode_id,
        phase_key=phase_key, invoking_pass=1, provider_key=provider,
        configured_model=model)
    db.finalize_llm_attempt(
        attempt_id, state='success', input_tokens=input_tokens,
        output_tokens=output_tokens)


def _backdate(db, run_id, stamp):
    conn = db.get_connection()
    conn.execute(
        "UPDATE llm_call_usage SET created_at = ?, finalized_at = ? WHERE run_id = ?",
        (stamp, stamp, run_id))
    conn.commit()


def _two_episodes(db):
    first = db.create_podcast('cost-feed-a', 'https://example.com/a.xml', 'Show A')
    second = db.create_podcast('cost-feed-b', 'https://example.com/b.xml', 'Show B')
    db.upsert_episode('cost-feed-a', 'ep-a', title='Episode A', status='processed')
    db.upsert_episode('cost-feed-b', 'ep-b', title='Episode B', status='processed')
    _seed_price(db, 'model-cheap', 2.0, 0.0)
    _seed_price(db, 'model-dear', 9.0, 0.0)
    _call(db, run_id='run-a1', podcast_id=first, episode_id='ep-a', model='model-cheap')
    _backdate(db, 'run-a1', '2026-01-01T00:00:00Z')
    _call(db, run_id='run-a2', podcast_id=first, episode_id='ep-a', model='model-cheap')
    _call(db, run_id='run-a2', podcast_id=first, episode_id='ep-a', model='model-dear',
          phase_key='review')
    _backdate(db, 'run-a2', '2026-02-01T00:00:00Z')
    _call(db, run_id='run-b1', podcast_id=second, episode_id='ep-b', model='model-cheap')
    _backdate(db, 'run-b1', '2026-03-01T00:00:00Z')
    return first, second


def test_row_shape_and_ordering_under_a_model_filter(temp_db):
    _two_episodes(temp_db)

    items, total = temp_db.get_episode_cost_stats(
        model='model-cheap', sort_by='lastActivityAt', sort_dir='desc')

    assert total == 2
    assert items == [
        {
            'podcastSlug': 'cost-feed-b',
            'podcastTitle': 'Show B',
            'episodeId': 'ep-b',
            'episodeTitle': 'Episode B',
            'modelsUsed': ['model-cheap'],
            'topModel': 'model-cheap',
            'runCount': 1,
            'latestRunCostUsd': '2.0',
            'latestRunUnknownCount': 0,
            'cumulativeCostUsd': '2.0',
            'unknownCostCount': 0,
            'hasUnknownCost': False,
            'lastActivityAt': '2026-03-01T00:00:00Z',
        },
        {
            'podcastSlug': 'cost-feed-a',
            'podcastTitle': 'Show A',
            'episodeId': 'ep-a',
            'episodeTitle': 'Episode A',
            'modelsUsed': ['model-cheap'],
            'topModel': 'model-cheap',
            'runCount': 2,
            # The latest run is reported whole, ignoring the model filter.
            'latestRunCostUsd': '11.0',
            'latestRunUnknownCount': 0,
            'cumulativeCostUsd': '4.0',
            'unknownCostCount': 0,
            'hasUnknownCost': False,
            'lastActivityAt': '2026-02-01T00:00:00Z',
        },
    ]


def test_ascending_sort_reverses_the_page(temp_db):
    _two_episodes(temp_db)

    items, _ = temp_db.get_episode_cost_stats(
        model='model-cheap', sort_by='lastActivityAt', sort_dir='asc')

    assert [item['episodeId'] for item in items] == ['ep-a', 'ep-b']


def test_pagination_keeps_the_total_and_splits_the_rows(temp_db):
    _two_episodes(temp_db)

    first_page, total = temp_db.get_episode_cost_stats(limit=1, page=1)
    second_page, second_total = temp_db.get_episode_cost_stats(limit=1, page=2)
    past_end, past_end_total = temp_db.get_episode_cost_stats(limit=1, page=9)

    assert (total, second_total, past_end_total) == (2, 2, 2)
    assert len(first_page) == len(second_page) == 1
    assert first_page[0]['episodeId'] != second_page[0]['episodeId']
    assert past_end == []


def test_one_page_reads_the_ledger_once(temp_db):
    """Total, aggregates and page come off a single pass, not one each."""
    _two_episodes(temp_db)
    conn = temp_db.get_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        temp_db.get_episode_cost_stats(limit=1, page=1, model='model-cheap')
    finally:
        conn.set_trace_callback(None)

    ledger_reads = [sql for sql in statements if 'llm_call_usage' in sql]
    assert len(ledger_reads) == 1
    assert ledger_reads[0].count('llm_call_usage') == 1
