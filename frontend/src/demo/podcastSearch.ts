import { HttpResponse } from 'msw/http';
import type { PodcastSearchResult } from '../api/podcastSearch';

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

export function mapItunesResults(json: { results?: unknown[] }): PodcastSearchResult[] {
  const out: PodcastSearchResult[] = [];
  for (const entry of json.results ?? []) {
    const r = entry as Record<string, unknown>;
    if (typeof r.feedUrl !== 'string' || r.feedUrl === '') continue;
    out.push({
      id: r.collectionId as number,
      title: str(r.collectionName),
      description: str(r.primaryGenreName),
      artworkUrl: str(r.artworkUrl600) || str(r.artworkUrl100),
      feedUrl: r.feedUrl,
      author: str(r.artistName),
      link: str(r.collectionViewUrl),
    });
  }
  return out;
}

export async function podcastSearchHandler({ request }: { request: Request }) {
  const q = new URL(request.url).searchParams.get('q') ?? '';
  if (!q.trim()) {
    return HttpResponse.json({ error: 'Query parameter "q" is required' }, { status: 400 });
  }
  try {
    const res = await fetch(
      'https://itunes.apple.com/search?' + new URLSearchParams({ term: q, media: 'podcast', limit: '10' }),
    );
    if (!res.ok) throw new Error(`iTunes responded ${res.status}`);
    return HttpResponse.json({ results: mapItunesResults(await res.json()), provider: 'itunes' });
  } catch {
    return HttpResponse.json({ error: 'Podcast search is unavailable' }, { status: 502 });
  }
}
