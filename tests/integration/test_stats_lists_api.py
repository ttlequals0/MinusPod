"""Integration tests for GET /stats/model-usage and GET /stats/episode-costs:
paginated, sortable, filterable list endpoints over the llm_call_usage ledger.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tests.app_bootstrap import bootstrap

bootstrap('stats_lists_test_')

from config import normalize_model_key  # noqa: E402


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _seed_price(db, model_id, input_cost, output_cost):
    db.upsert_fetched_pricing([{
        'match_key': normalize_model_key(model_id),
        'raw_model_id': model_id,
        'display_name': model_id,
        'input_cost_per_mtok': input_cost,
        'output_cost_per_mtok': output_cost,
    }], source='litellm')


def _call(db, *, run_id, podcast_id, episode_id, provider_key, configured_model,
          state='success', input_tokens=1_000_000, output_tokens=0, phase_key='detect'):
    attempt_id = db.begin_llm_attempt(
        run_id=run_id, podcast_id=podcast_id, episode_id=episode_id,
        phase_key=phase_key, invoking_pass=1, provider_key=provider_key,
        configured_model=configured_model)
    return db.finalize_llm_attempt(
        attempt_id, state=state, input_tokens=input_tokens, output_tokens=output_tokens)


def _backdate_run(db, run_id, created_at, finalized_at):
    conn = db.get_connection()
    conn.execute(
        "UPDATE llm_call_usage SET created_at = ?, finalized_at = ? WHERE run_id = ?",
        (created_at, finalized_at, run_id)
    )
    conn.commit()


class TestModelUsageStats:
    def test_groups_identical_model_under_two_providers_separately(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-group', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-group', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        _seed_price(temp_db, 'shared-model', 2.0, 0.0)
        _call(temp_db, run_id='r1', podcast_id=podcast_id, episode_id='ep1',
              provider_key='openai', configured_model='shared-model')
        _call(temp_db, run_id='r2', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='shared-model')

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-group')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 2
        rows = {(r['provider'], r['model']): r for r in data['items']}
        assert ('openai', 'shared-model') in rows
        assert ('anthropic', 'shared-model') in rows
        assert rows[('openai', 'shared-model')]['knownCostUsd'] == '2.0'
        assert rows[('openai', 'shared-model')]['distinctEpisodes'] == 1

    def test_unknown_sort_by_falls_back_to_a_safe_default(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-sort', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-sort', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        _seed_price(temp_db, 'sort-model', 1.0, 0.0)
        _call(temp_db, run_id='r-sort', podcast_id=podcast_id, episode_id='ep1',
              provider_key='openai', configured_model='sort-model')

        resp = app_client.get(
            '/api/v1/stats/model-usage?podcastSlug=pod-sort&sortBy=drop%20table')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1

    def test_filters_apply_in_sql_from_to_provider_and_model(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-filter', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-filter', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        _seed_price(temp_db, 'old-model', 1.0, 0.0)
        _seed_price(temp_db, 'new-model', 1.0, 0.0)
        _call(temp_db, run_id='r-old', podcast_id=podcast_id, episode_id='ep1',
              provider_key='openai', configured_model='old-model')
        _backdate_run(temp_db, 'r-old', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')
        _call(temp_db, run_id='r-new', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='new-model')

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-filter&from=2025-01-01T00:00:00Z')
        assert resp.status_code == 200
        rows = resp.get_json()['items']
        assert {r['model'] for r in rows} == {'new-model'}

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-filter&provider=openai')
        rows = resp.get_json()['items']
        assert {r['model'] for r in rows} == {'old-model'}

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-filter&model=new-model')
        rows = resp.get_json()['items']
        assert {r['provider'] for r in rows} == {'anthropic'}

    def test_pagination_reports_total_and_total_pages(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-page', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-page', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        for idx in range(3):
            model_id = f'page-model-{idx}'
            _seed_price(temp_db, model_id, 1.0, 0.0)
            _call(temp_db, run_id=f'r-page-{idx}', podcast_id=podcast_id, episode_id='ep1',
                  provider_key='openai', configured_model=model_id)

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-page&limit=1&page=1')
        data = resp.get_json()
        assert data['total'] == 3
        assert data['totalPages'] == 3
        assert len(data['items']) == 1

        resp2 = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-page&limit=1&page=2')
        data2 = resp2.get_json()
        assert data2['items'][0]['model'] != data['items'][0]['model']

    def test_unknown_cost_rows_are_counted_not_zeroed(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-unknown', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-unknown', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        # No pricing seeded for this model -> cost_source='unknown', cost_usd NULL.
        _call(temp_db, run_id='r-unpriced', podcast_id=podcast_id, episode_id='ep1',
              provider_key='openai', configured_model='unpriced-model')

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-unknown&model=unpriced-model')
        row = resp.get_json()['items'][0]
        assert row['calls'] == 1
        assert row['unknownCostCount'] == 1
        assert row['knownCostUsd'] == '0'

    def test_billed_failure_is_included_in_spend(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-fail', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-fail', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        _seed_price(temp_db, 'fail-model', 1.0, 0.0)
        _call(temp_db, run_id='r-fail', podcast_id=podcast_id, episode_id='ep1',
              provider_key='openai', configured_model='fail-model', state='failure')

        resp = app_client.get('/api/v1/stats/model-usage?podcastSlug=pod-fail&model=fail-model')
        row = resp.get_json()['items'][0]
        assert row['calls'] == 1
        assert row['knownCostUsd'] == '1.0'


class TestEpisodeCostStats:
    def test_distinguishes_latest_run_from_cumulative_cost(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-ep', 'https://example.com/feed.xml', 'Pod Ep')
        temp_db.upsert_episode('pod-ep', 'ep1', original_url='https://example.com/e.mp3',
                               title='Episode One', status='processed')
        _seed_price(temp_db, 'model-a', 2.0, 0.0)
        _seed_price(temp_db, 'model-b', 5.0, 0.0)
        _call(temp_db, run_id='run-a', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='model-a')
        _backdate_run(temp_db, 'run-a', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')
        _call(temp_db, run_id='run-b', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='model-b')
        _backdate_run(temp_db, 'run-b', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')

        resp = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-ep')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        item = data['items'][0]
        assert item['podcastSlug'] == 'pod-ep'
        assert item['podcastTitle'] == 'Pod Ep'
        assert item['episodeId'] == 'ep1'
        assert item['episodeTitle'] == 'Episode One'
        assert item['runCount'] == 2
        assert set(item['modelsUsed']) == {'model-a', 'model-b'}
        assert item['latestRunCostUsd'] == '5.0'
        assert item['cumulativeCostUsd'] == '7.0'
        assert item['lastActivityAt'] == '2024-01-01T00:00:00Z'

    def test_latest_run_unknown_count_is_scoped_to_the_latest_run(self, app_client, temp_db):
        """An unpriced old run must not flag the fully-priced latest run's
        column as incomplete (F09 latest-vs-cumulative scoping)."""
        _authed(app_client)
        pid = temp_db.create_podcast('pod-scope', 'https://example.com/s.xml', 'Pod Scope')
        temp_db.upsert_episode('pod-scope', 'ep1', original_url='https://example.com/e.mp3',
                               title='Episode One', status='processed')
        _seed_price(temp_db, 'priced-model', 2.0, 0.0)
        _call(temp_db, run_id='run-old', podcast_id=pid, episode_id='ep1',
              provider_key='anthropic', configured_model='unpriced-old')
        _backdate_run(temp_db, 'run-old', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')
        _call(temp_db, run_id='run-new', podcast_id=pid, episode_id='ep1',
              provider_key='anthropic', configured_model='priced-model')
        _backdate_run(temp_db, 'run-new', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')

        resp = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-scope')
        item = resp.get_json()['items'][0]
        assert item['hasUnknownCost'] is True
        assert item['unknownCostCount'] == 1
        assert item['latestRunUnknownCount'] == 0
        assert item['latestRunCostUsd'] == '2.0'

    def test_episode_cost_row_flags_unknown_cost(self, app_client, temp_db):
        """An episode with an unpriced call must report hasUnknownCost so its
        amount is not presented as complete (F09)."""
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-unk', 'https://example.com/u.xml', 'Pod Unk')
        temp_db.upsert_episode('pod-unk', 'ep1', original_url='https://example.com/e.mp3',
                               title='Episode One', status='processed')
        # No pricing seeded for this model: the call finalizes with unknown cost.
        _call(temp_db, run_id='run-u', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='unpriced-model')

        resp = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-unk')
        assert resp.status_code == 200
        item = resp.get_json()['items'][0]
        assert item['hasUnknownCost'] is True
        assert item['unknownCostCount'] == 1

    def test_latest_run_is_not_reordered_by_provider_or_model_filter(self, app_client, temp_db):
        """Filtering to an older run's model must not make that run look
        like the episode's latest: latestRunCostUsd/lastActivityAt always
        come from the true latest run, while cumulativeCostUsd and
        modelsUsed still narrow to the filtered model only."""
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-reorder', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-reorder', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        _seed_price(temp_db, 'model-a', 2.0, 0.0)
        _seed_price(temp_db, 'model-b', 5.0, 0.0)
        _call(temp_db, run_id='run-old-a', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='model-a')
        _backdate_run(temp_db, 'run-old-a', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')
        _call(temp_db, run_id='run-new-b', podcast_id=podcast_id, episode_id='ep1',
              provider_key='anthropic', configured_model='model-b')
        _backdate_run(temp_db, 'run-new-b', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')

        resp = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-reorder&model=model-a')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        item = data['items'][0]
        # True latest run is run-new-b (model-b), even though the model
        # filter only matches run-old-a.
        assert item['latestRunCostUsd'] == '5.0'
        assert item['lastActivityAt'] == '2024-01-01T00:00:00Z'
        # Cumulative cost and modelsUsed stay scoped to the filtered model.
        assert item['cumulativeCostUsd'] == '2.0'
        assert item['modelsUsed'] == ['model-a']

    def test_pagination_and_filters(self, app_client, temp_db):
        _authed(app_client)
        pod1 = temp_db.create_podcast('pod-ep-a', 'https://example.com/a.xml', 'A')
        pod2 = temp_db.create_podcast('pod-ep-b', 'https://example.com/b.xml', 'B')
        temp_db.upsert_episode('pod-ep-a', 'ea1', original_url='https://example.com/a1.mp3',
                               title='A1', status='processed')
        temp_db.upsert_episode('pod-ep-b', 'eb1', original_url='https://example.com/b1.mp3',
                               title='B1', status='processed')
        _seed_price(temp_db, 'model-x', 1.0, 0.0)
        _call(temp_db, run_id='run-pod-a', podcast_id=pod1, episode_id='ea1',
              provider_key='openai', configured_model='model-x')
        _call(temp_db, run_id='run-pod-b', podcast_id=pod2, episode_id='eb1',
              provider_key='openai', configured_model='model-x')

        resp = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-ep-a')
        data = resp.get_json()
        assert data['total'] == 1
        assert data['items'][0]['episodeId'] == 'ea1'

        resp = app_client.get('/api/v1/stats/episode-costs?model=model-x&limit=1&page=1')
        page1 = resp.get_json()
        assert page1['total'] == 2
        assert page1['totalPages'] == 2

        resp2 = app_client.get('/api/v1/stats/episode-costs?model=model-x&limit=1&page=2')
        page2 = resp2.get_json()
        assert page1['items'][0]['episodeId'] != page2['items'][0]['episodeId']

    def test_top_model_is_the_most_expensive(self, app_client, temp_db):
        _authed(app_client)
        pid = temp_db.create_podcast('pod-top', 'https://example.com/t.xml', 'Top')
        temp_db.upsert_episode('pod-top', 'et1', original_url='https://example.com/e.mp3',
                               title='ET1', status='processed')
        _seed_price(temp_db, 'cheap-model', 2.0, 0.0)
        _seed_price(temp_db, 'pricey-model', 15.0, 0.0)
        _call(temp_db, run_id='run-top', podcast_id=pid, episode_id='et1',
              provider_key='anthropic', configured_model='cheap-model', phase_key='detection')
        _call(temp_db, run_id='run-top', podcast_id=pid, episode_id='et1',
              provider_key='anthropic', configured_model='pricey-model', phase_key='review')

        item = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-top').get_json()['items'][0]
        assert item['topModel'] == 'pricey-model'
        assert set(item['modelsUsed']) == {'cheap-model', 'pricey-model'}

    def test_all_unknown_cost_top_model_is_alphabetically_first(self, app_client, temp_db):
        _authed(app_client)
        pid = temp_db.create_podcast('pod-unk-top', 'https://example.com/u.xml', 'UnkTop')
        temp_db.upsert_episode('pod-unk-top', 'eu1', original_url='https://example.com/e.mp3',
                               title='EU1', status='processed')
        _call(temp_db, run_id='run-unk', podcast_id=pid, episode_id='eu1',
              provider_key='openai-compatible', configured_model='zeta-model')
        _call(temp_db, run_id='run-unk', podcast_id=pid, episode_id='eu1',
              provider_key='openai-compatible', configured_model='alpha-model')

        item = app_client.get('/api/v1/stats/episode-costs?podcastSlug=pod-unk-top').get_json()['items'][0]
        assert item['topModel'] == 'alpha-model'


class TestEpisodeCostRuns:
    def test_returns_run_and_phase_breakdown(self, app_client, temp_db):
        _authed(app_client)
        pid = temp_db.create_podcast('pod-runs', 'https://example.com/r.xml', 'Runs')
        temp_db.upsert_episode('pod-runs', 'er1', original_url='https://example.com/e.mp3',
                               title='ER1', status='processed')
        _seed_price(temp_db, 'm-det', 3.0, 0.0)
        _seed_price(temp_db, 'm-rev', 5.0, 0.0)
        _call(temp_db, run_id='run-r1', podcast_id=pid, episode_id='er1',
              provider_key='anthropic', configured_model='m-det', phase_key='detection')
        _call(temp_db, run_id='run-r1', podcast_id=pid, episode_id='er1',
              provider_key='openai-compatible', configured_model='m-rev', phase_key='review')
        temp_db.record_processing_history(
            podcast_id=pid, podcast_slug='pod-runs', podcast_title='Runs',
            episode_id='er1', episode_title='ER1', status='completed', run_id='run-r1')

        resp = app_client.get('/api/v1/stats/episode-costs/runs?slug=pod-runs&episodeId=er1')
        assert resp.status_code == 200
        runs = resp.get_json()['runs']
        assert len(runs) >= 1
        phases = runs[-1]['phases']
        assert {p['phaseKey'] for p in phases} >= {'detection', 'review'}
        assert {p['configuredModel'] for p in phases} >= {'m-det', 'm-rev'}

    def test_unknown_episode_is_404(self, app_client, temp_db):
        _authed(app_client)
        temp_db.create_podcast('pod-404', 'https://example.com/x.xml', 'X')
        resp = app_client.get('/api/v1/stats/episode-costs/runs?slug=pod-404&episodeId=nope')
        assert resp.status_code == 404

    def test_missing_params_is_400(self, app_client, temp_db):
        _authed(app_client)
        resp = app_client.get('/api/v1/stats/episode-costs/runs?slug=pod-404')
        assert resp.status_code == 400


class TestLedgerDateParams:
    def test_bare_dates_cover_the_whole_utc_day(self, app_client, temp_db):
        """A bare from/to date passes through to the db canonicalizer, so a
        call in the final second of the selected day is still included."""
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-day', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-day', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        _seed_price(temp_db, 'day-model', 1.0, 0.0)
        _call(temp_db, run_id='r-late', podcast_id=podcast_id, episode_id='ep1',
              provider_key='openai', configured_model='day-model')
        _backdate_run(temp_db, 'r-late', '2025-03-04T23:59:59.500Z',
                      '2025-03-04T23:59:59.500Z')

        resp = app_client.get(
            '/api/v1/stats/model-usage?podcastSlug=pod-day&from=2025-03-04&to=2025-03-04')
        assert resp.status_code == 200
        assert {r['model'] for r in resp.get_json()['items']} == {'day-model'}

    def test_unparseable_from_date_is_rejected(self, app_client):
        _authed(app_client)
        resp = app_client.get('/api/v1/stats/model-usage?from=not-a-date')
        assert resp.status_code == 400
        assert 'from' in resp.get_json()['error']

    def test_unparseable_to_date_is_rejected_on_episode_costs(self, app_client):
        _authed(app_client)
        resp = app_client.get('/api/v1/stats/episode-costs?to=13/04/2025')
        assert resp.status_code == 400
        assert 'to' in resp.get_json()['error']


class TestLedgerFilterOptions:
    def test_returns_values_beyond_the_first_list_page(self, app_client, temp_db):
        _authed(app_client)
        podcast_id = temp_db.create_podcast('pod-opts', 'https://example.com/feed.xml', 'Pod')
        temp_db.upsert_episode('pod-opts', 'ep1', original_url='https://example.com/e.mp3',
                               title='Ep1', status='processed')
        for idx, provider in enumerate(('openai', 'anthropic', 'openrouter')):
            model_id = f'opt-model-{idx}'
            _seed_price(temp_db, model_id, 1.0, 0.0)
            _call(temp_db, run_id=f'r-opt-{idx}', podcast_id=podcast_id, episode_id='ep1',
                  provider_key=provider, configured_model=model_id)

        listed = app_client.get(
            '/api/v1/stats/model-usage?podcastSlug=pod-opts&limit=1&page=1').get_json()
        assert len(listed['items']) == 1

        resp = app_client.get('/api/v1/stats/ledger-filter-options?podcastSlug=pod-opts')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['providers'] == ['anthropic', 'openai', 'openrouter']
        assert sorted(p['model'] for p in data['pairs']) == [
            'opt-model-0', 'opt-model-1', 'opt-model-2']
        assert {'provider': 'anthropic', 'model': 'opt-model-1'} in data['pairs']

    def test_scoped_by_podcast_and_date(self, app_client, temp_db):
        _authed(app_client)
        pod1 = temp_db.create_podcast('pod-opt-a', 'https://example.com/a.xml', 'A')
        pod2 = temp_db.create_podcast('pod-opt-b', 'https://example.com/b.xml', 'B')
        temp_db.upsert_episode('pod-opt-a', 'ea1', original_url='https://example.com/a1.mp3',
                               title='A1', status='processed')
        temp_db.upsert_episode('pod-opt-b', 'eb1', original_url='https://example.com/b1.mp3',
                               title='B1', status='processed')
        _seed_price(temp_db, 'opt-a-model', 1.0, 0.0)
        _seed_price(temp_db, 'opt-b-model', 1.0, 0.0)
        _call(temp_db, run_id='r-opt-a-old', podcast_id=pod1, episode_id='ea1',
              provider_key='openai', configured_model='opt-a-model')
        _backdate_run(temp_db, 'r-opt-a-old', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')
        _call(temp_db, run_id='r-opt-b', podcast_id=pod2, episode_id='eb1',
              provider_key='anthropic', configured_model='opt-b-model')

        resp = app_client.get('/api/v1/stats/ledger-filter-options?podcastSlug=pod-opt-a')
        assert resp.get_json()['pairs'] == [
            {'provider': 'openai', 'model': 'opt-a-model'}]

        resp = app_client.get(
            '/api/v1/stats/ledger-filter-options?podcastSlug=pod-opt-a&from=2025-01-01')
        assert resp.get_json()['pairs'] == []

    def test_unparseable_date_is_rejected(self, app_client):
        _authed(app_client)
        resp = app_client.get('/api/v1/stats/ledger-filter-options?from=yesterday')
        assert resp.status_code == 400
