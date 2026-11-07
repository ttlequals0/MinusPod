import type { JsonBodyType, RequestHandler } from 'msw';
import { http, HttpResponse } from 'msw/http';
import { resolveFixture, type Fixture } from './fixtures';
import { DEMO_WRITE_ERROR } from './copy';
import { searchHandler, searchStatsHandler } from './search';
import { podcastSearchHandler } from './podcastSearch';

const API_PREFIX = '/api/v1';

const DOWNLOAD_PATHS = new Set([
  '/system/backup',
  '/system/config-export',
  '/system/config-backup',
  '/system/diagnostic-export',
  '/history/export',
  '/feeds/export-opml',
  '/patterns/export',
]);
const DOWNLOAD_PATTERNS = [/^\/cue-templates\/[^/]+\/(export|audio)$/, /\.mp3$/];
const RUN_LOG = /\/runs\/\d+\/log$/;

export function forbidden(): Response {
  return HttpResponse.json({ error: DEMO_WRITE_ERROR }, { status: 403 });
}

function fixtureResponse(f: Fixture): Response {
  if (f.status === 204 || f.status === 304) return new HttpResponse(null, { status: f.status });
  return HttpResponse.json(f.body as JsonBodyType, { status: f.status });
}

export function isDownload(pathname: string, params: URLSearchParams): boolean {
  if (DOWNLOAD_PATHS.has(pathname)) return true;
  if (DOWNLOAD_PATTERNS.some((re) => re.test(pathname))) return true;
  return RUN_LOG.test(pathname) && params.get('format') === 'raw';
}

export const handlers: RequestHandler[] = [
  http.get('*/api/v1/podcast-search', podcastSearchHandler),
  http.get('*/api/v1/search', searchHandler),
  http.get('*/api/v1/search/stats', searchStatsHandler),
  http.get('*/api/v1/*', async ({ request }) => {
    const url = new URL(request.url);
    const path = url.pathname.startsWith(API_PREFIX) ? url.pathname.slice(API_PREFIX.length) : url.pathname;
    if (isDownload(path, url.searchParams)) return forbidden();
    const f = await resolveFixture(url);
    return fixtureResponse(f);
  }),
  http.all('*/api/v1/*', () => forbidden()),
];
