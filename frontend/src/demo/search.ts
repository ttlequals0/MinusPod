import type { JsonBodyType } from 'msw';
import { HttpResponse } from 'msw/http';
import { emptyDefault } from './fixtures';

export async function searchHandler({ request }: { request: Request }) {
  void request;
  return HttpResponse.json(emptyDefault('/search').body as JsonBodyType);
}

export async function searchStatsHandler() {
  return HttpResponse.json(emptyDefault('/search/stats').body as JsonBodyType);
}
