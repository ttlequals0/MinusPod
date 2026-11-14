import io
import json
import types
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image

from scripts.demo.leak_check import check_text
from scripts.demo.record_live import (
    KEY_PARAM, QUEUE_KEY, Abort, VALID_LLM_PROVIDERS, encode_uri_component,
    file_for, fixture_key, run, scrub_body, scrub_providers, synthesize_queue, to_jpeg,
)

VECTORS = Path(__file__).parents[2] / 'scripts/demo/crawl/lib/key-vectors.json'


def test_scrub_providers_replaces_base_urls():
    body = {'cryptoReady': True, 'openai': {'configured': True, 'source': 'db', 'baseUrl': 'https://llm.internal.example:8080/v1'},
            'anthropic': {'baseUrl': ''}}
    out = scrub_providers(body)
    assert out['openai']['baseUrl'] == 'https://llm.example.com'
    assert out['anthropic']['baseUrl'] == ''


def test_fixture_key_matches_shared_vectors():
    vectors = json.loads(VECTORS.read_text())
    assert len(vectors) >= 8
    for url, expected in vectors.items():
        assert fixture_key(url) == expected, url


def test_file_for_is_sha1_json():
    assert file_for('/x').endswith('.json') and len(file_for('/x')) == 45


def test_scrub_body_masks_feed_keys():
    body = {'url': 'https://h/rss?key=abcdefghijklmnopqrstuvwx&a=1'}
    out = scrub_body(body)
    assert out['url'] == 'https://h/rss?key=demo&a=1'
    assert not check_text(json.dumps(out), 'x', [])
    assert KEY_PARAM.sub(r'\1demo', 'a?key=zz') == 'a?key=demo'


def test_to_jpeg_downsizes_to_600():
    buf = io.BytesIO()
    Image.new('RGBA', (1200, 800)).save(buf, 'PNG')
    img = Image.open(io.BytesIO(to_jpeg(buf.getvalue())))
    assert img.format == 'JPEG' and img.mode == 'RGB' and max(img.size) == 600


def test_synthesize_queue_stages():
    hist = [{'podcastSlug': s, 'podcastTitle': 'T', 'episodeId': f'e{i}', 'episodeTitle': 'E'}
            for i, s in enumerate(['a', 'b', 'c'])]
    rows = synthesize_queue(hist, {'b'}, datetime(2026, 11, 7))
    assert [r['slug'] for r in rows] == ['a', 'c']
    assert [r['stage'] for r in rows] == ['queued', 'transcribing']


def test_scrub_body_handles_nested_json_strings():
    inner = json.dumps({'u': 'https://h/rss?key=abcdefghijklmnop', 'n': 1})
    out = scrub_body({'raw': inner, 'list': ['x?key=zzzz']})
    assert json.loads(out['raw'])['u'] == 'https://h/rss?key=demo'
    assert out['list'] == ['x?key=demo']


def test_to_jpeg_flattens_alpha_and_palette():
    buf = io.BytesIO()
    Image.new('RGBA', (4, 4), (255, 0, 0, 0)).save(buf, 'PNG')
    assert Image.open(io.BytesIO(to_jpeg(buf.getvalue()))).getpixel((1, 1)) == (255, 255, 255)
    buf = io.BytesIO()
    Image.new('P', (4, 4)).save(buf, 'PNG')
    assert Image.open(io.BytesIO(to_jpeg(buf.getvalue()))).mode == 'RGB'


def test_encode_uri_component():
    assert encode_uri_component("a b/c~!'()*é") == "a%20b%2Fc~!'()*%C3%A9"


def test_synthesize_queue_takes_three_after_exclusions():
    hist = [{'podcastSlug': 'x' if i < 5 else 'ok', 'podcastTitle': 'T', 'episodeId': f'e{i}', 'episodeTitle': 'E'}
            for i in range(10)]
    rows = synthesize_queue(hist, {'x'}, datetime(2026, 11, 7))
    assert len(rows) == 3 and {r['slug'] for r in rows} == {'ok'}


def png(color='red'):
    buf = io.BytesIO()
    Image.new('RGB', (800, 800), color).save(buf, 'PNG')
    return buf.getvalue()


def fake_get(extra_body=None, queue=None, feed_art=None):
    today = date.today().isoformat() + 'T00:00:00Z'
    eps = [{'id': 'ep/1', 'artworkUrl': 'u', 'published': today, 'status': 'completed'},
           {'id': 'ep3', 'artworkUrl': 'u', 'published': today, 'status': 'processed'},
           {'id': 'ep4', 'artworkUrl': None, 'published': today, 'status': 'completed'},
           {'id': 'ep2', 'artworkUrl': None, 'published': today}]

    def resp(body=None, status=200, content=b''):
        return types.SimpleNamespace(
            status_code=status, ok=status < 400, content=content, json=lambda: body)

    def get(path):
        if path == '/settings/providers':
            return resp({'cryptoReady': True, 'openai': {'baseUrl': 'https://x.internal'}, **(extra_body or {})})
        if path == '/settings/provider-budget/currencies':
            return resp({'currencies': [{'code': 'USD'}]})
        if path == '/system/updates':
            return resp({'currentVersion': '1.0'})
        if path == '/settings/models?provider=anthropic&slot=primary':
            return resp({'models': [{'id': 'a'}, {'id': 'b'}]})
        if path == '/settings/models?provider=anthropic&slot=secondary':
            return resp({'models': [{'id': 'b'}, {'id': 'c'}]})
        if path == '/settings/models?provider=anthropic&slot=failover':
            return resp({'error': 'no key'}, status=400)
        if path == '/settings/models?provider=openrouter&slot=primary':
            return resp({'models': [{'id': 'o'}]})
        if path == '/settings/models?provider=openrouter&slot=secondary':
            return resp(status=500)
        if path == '/settings/models?provider=typesafe&slot=primary':
            return resp({'models': [{'id': 't'}]})
        if path == '/settings/models?provider=typesafe&slot=failover':
            return resp({'error': 'Failover LLM is chat-only'}, status=400)
        if path.startswith('/settings/'):
            return resp({'models': []})
        if path == QUEUE_KEY:
            return resp(queue if queue is not None else [
                {'slug': 'skip', 'episodeId': 'q', 'title': 't', 'podcast': 'p', 'startedAt': None}])
        if path.startswith('/feeds?'):
            return resp({'feeds': [{'slug': 'keep'}, {'slug': 'skip'}]})
        if path == '/feeds/keep/artwork':
            return resp(content=feed_art or png())
        if path.startswith('/feeds/keep/episodes?'):
            return resp({'episodes': eps})
        if path == '/feeds/keep/episodes/ep%2F1/artwork':
            return resp(content=png('blue'))
        if path == '/feeds/keep/episodes/ep4/peaks?resolution_ms=250':
            return resp({'peaks': [0.5]})
        if path == '/feeds/keep/episodes/ep%2F1/peaks?resolution_ms=250':
            return resp({'peaks': [0.123456, [0.5, 1]], 'start': 12.3456789})
        if path.endswith('/peaks?resolution_ms=250'):
            return resp(status=404)
        if path.endswith('/artwork'):
            return resp(status=404)
        raise AssertionError(path)
    return get


def args_for(tmp_path, **kw):
    excl = tmp_path / 'ex.json'
    excl.write_text(json.dumps({'slugs': ['skip']}))
    base = dict(out=tmp_path / 'out', hosts='leak.example.org', exclusions=excl, since_days=30,
                synthesize_queue=False, peaks=True, base_url='', cookies=None, clean=False)
    return types.SimpleNamespace(**{**base, **kw})


def test_run_writes_layout(tmp_path):
    assert run(args_for(tmp_path), get=fake_get()) == 0
    out = tmp_path / 'out'
    index = json.loads((out / 'overrides' / 'index.json').read_text())
    assert QUEUE_KEY in index and '/settings/providers' in index
    assert '/settings/provider-budget/currencies' in index and '/system/updates' in index
    assert all((out / 'overrides' / f).exists() for f in index.values())
    providers = json.loads((out / 'overrides' / index['/settings/providers']).read_text())
    assert providers['body']['openai']['baseUrl'] == 'https://llm.example.com'
    queue = json.loads((out / 'overrides' / index[QUEUE_KEY]).read_text())
    assert queue['body'] == []
    assert (out / 'artwork/feeds/keep.jpg').exists()
    assert (out / 'artwork/episodes/keep/ep%2F1.jpg').exists()
    assert not (out / 'artwork/feeds/skip.jpg').exists()
    assert sorted(p.name for p in (out / 'artwork/episodes/keep').iterdir()) == ['ep%2F1.jpg']


def test_run_unions_model_lists_across_slots(tmp_path):
    assert run(args_for(tmp_path), get=fake_get()) == 0
    out = tmp_path / 'out'
    index = json.loads((out / 'overrides' / 'index.json').read_text())
    for slot in ('primary', 'secondary', 'failover'):
        rec = json.loads((out / 'overrides' / index[f'/settings/models?provider=anthropic&slot={slot}']).read_text())
        assert rec['status'] == 200
        assert [m['id'] for m in rec['body']['models']] == ['a', 'b', 'c']
    empty = json.loads((out / 'overrides' / index['/settings/models?provider=ollama&slot=primary']).read_text())
    assert empty == {'status': 200, 'body': {'models': []}}
    rec = lambda key: json.loads((out / 'overrides' / index[key]).read_text())  # noqa: E731
    assert rec('/settings/models?provider=openrouter&slot=secondary') == {'status': 200, 'body': {'models': [{'id': 'o'}]}}
    assert rec('/settings/models?provider=typesafe&slot=secondary')['body'] == {'models': [{'id': 't'}]}
    assert rec('/settings/models?provider=typesafe&slot=failover') == {'status': 400, 'body': {'error': 'Failover LLM is chat-only'}}
    assert sum(k.startswith('/settings/models?') for k in index) == len(VALID_LLM_PROVIDERS) * 3


def test_run_leak_hit_exits_2_without_more_files(tmp_path):
    leaky = {'note': 'see leak.example.org'}
    try:
        run(args_for(tmp_path), get=fake_get(extra_body=leaky))
    except Abort as e:
        assert e.code == 2
    else:
        raise AssertionError('no abort')
    out = tmp_path / 'out'
    assert not (out / 'overrides').exists() and not (out / 'artwork').exists()


def test_run_unreadable_feed_art_aborts(tmp_path):
    try:
        run(args_for(tmp_path), get=fake_get(feed_art=b'not an image'))
    except Abort as e:
        assert e.code == 2
    else:
        raise AssertionError('no abort')


def test_run_records_rounded_peaks_and_skips_404(tmp_path):
    assert run(args_for(tmp_path), get=fake_get()) == 0
    overrides = tmp_path / 'out' / 'overrides'
    index = json.loads((overrides / 'index.json').read_text())
    key = '/feeds/keep/episodes/ep%2F1/peaks'
    body = json.loads((overrides / index[key]).read_text())['body']
    assert body['peaks'] == [0.123, [0.5, 1]]
    assert body['start'] == 12.3456789
    assert not any('ep3' in k for k in index)
    assert '/feeds/keep/episodes/ep4/peaks' in index


def test_run_no_peaks(tmp_path):
    run(args_for(tmp_path, peaks=False), get=fake_get())
    index = json.loads((tmp_path / 'out' / 'overrides' / 'index.json').read_text())
    assert not any('/peaks' in k for k in index)


def abort_code(args, get):
    with pytest.raises(Abort) as e:
        run(args, get=get)
    return e.value.code, str(e.value)


def test_non_empty_out_is_refused_unless_clean(tmp_path):
    out = tmp_path / 'out'
    (out / 'artwork' / 'feeds').mkdir(parents=True)
    stale = out / 'artwork' / 'feeds' / 'old.jpg'
    stale.write_bytes(b'x')
    code, msg = abort_code(args_for(tmp_path), fake_get())
    assert code == 1 and '--clean' in msg
    assert stale.exists() and not (out / 'overrides').exists()
    assert run(args_for(tmp_path, clean=True), get=fake_get()) == 0
    assert not stale.exists() and (out / 'overrides' / 'index.json').exists()


def test_unmatched_exclusion_slug_aborts_before_writing(tmp_path):
    (tmp_path / 'ex2.json').write_text(json.dumps({'slugs': ['skip', 'typo']}))
    code, msg = abort_code(args_for(tmp_path, exclusions=tmp_path / 'ex2.json'), fake_get())
    assert code == 1 and 'typo' in msg and 'skip' not in msg
    assert not (tmp_path / 'out').exists()
    stale = tmp_path / 'out' / 'stale.json'
    stale.parent.mkdir()
    stale.write_text('{}')
    code, _ = abort_code(args_for(tmp_path, exclusions=tmp_path / 'ex2.json', clean=True), fake_get())
    assert code == 1 and stale.exists()


@pytest.mark.parametrize('hosts', ['', '/tmp/hosts.txt', 'localhost'])
def test_bad_hosts_abort(tmp_path, hosts):
    assert abort_code(args_for(tmp_path, hosts=hosts), fake_get())[0] == 1


def test_non_canonical_override_key_aborts(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.demo.record_live.PLAIN_TARGETS', ['/settings/models?slot=primary&provider=x'])
    code, msg = abort_code(args_for(tmp_path), fake_get())
    assert code == 1 and 'canonical' in msg
