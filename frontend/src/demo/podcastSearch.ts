import type { JsonBodyType } from 'msw';
import { HttpResponse } from 'msw/http';
import { emptyDefault } from './fixtures';

export async function podcastSearchHandler({ request }: { request: Request }) {
  void request;
  return HttpResponse.json(emptyDefault('/podcast-search').body as JsonBodyType, { status: 200 });
}
