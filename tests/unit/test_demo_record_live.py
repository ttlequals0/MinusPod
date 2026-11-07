import io
import json
from datetime import date
from pathlib import Path

from PIL import Image

from scripts.demo.leak_check import check_text
from scripts.demo.record_live import (
    KEY_PARAM, episode_artwork_targets, file_for, fixture_key, override_targets,
    scrub_body, scrub_providers, synthesize_queue, to_jpeg,
)

VECTORS = Path(__file__).parents[2] / 'scripts/demo/crawl/lib/key-vectors.json'


def test_override_targets_enumerate_models_per_provider_and_slot():
    targets = override_targets({'anthropic': {'configured': True}, 'ollama': {'configured': False}, 'cryptoReady': True})
    assert '/settings/providers' in targets
    assert '/settings/models?provider=anthropic&slot=primary' in targets
    assert '/settings/models?provider=ollama&slot=failover' in targets
    assert not any('cryptoReady' in t for t in targets)
    assert '/settings/whisper-models' in targets


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


def test_episode_artwork_targets_filters_by_date_and_artwork():
    eps = [
        {'id': 'new', 'artworkUrl': 'u', 'published': '2026-11-01T00:00:00Z'},
        {'id': 'old', 'artworkUrl': 'u', 'published': '2026-01-01T00:00:00Z'},
        {'id': 'noart', 'artworkUrl': None, 'published': '2026-11-01T00:00:00Z'},
        {'id': 'fallback', 'artworkUrl': 'u', 'published': '', 'createdAt': '2026-10-20T10:00:00'},
    ]
    got = [e['id'] for e in episode_artwork_targets(eps, 30, date(2026, 11, 7))]
    assert got == ['new', 'fallback']


def test_to_jpeg_downsizes_to_600():
    buf = io.BytesIO()
    Image.new('RGBA', (1200, 800)).save(buf, 'PNG')
    img = Image.open(io.BytesIO(to_jpeg(buf.getvalue())))
    assert img.format == 'JPEG' and img.mode == 'RGB' and max(img.size) == 600


def test_synthesize_queue_stages():
    from datetime import datetime
    hist = [{'podcastSlug': s, 'podcastTitle': 'T', 'episodeId': f'e{i}', 'episodeTitle': 'E'}
            for i, s in enumerate(['a', 'b', 'c'])]
    rows = synthesize_queue(hist, {'b'}, datetime(2026, 11, 7))
    assert [r['slug'] for r in rows] == ['a', 'c']
    assert [r['stage'] for r in rows] == ['queued', 'transcribing']
