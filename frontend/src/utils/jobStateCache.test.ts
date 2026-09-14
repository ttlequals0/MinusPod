import { describe, it, expect } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { ApiError, jobStateOf } from '../api/client';
import { applyEpisodeJobState, jobStateFromError } from './jobStateCache';
import { isActionBlocked } from './processingStage';

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe('jobStateOf / jobStateFromError', () => {
  it('reads the jobState a success body reported', () => {
    expect(jobStateOf({ message: 'ok', jobState: 'queued' })).toBe('queued');
  });

  it('reads the jobState a 409 reported', () => {
    expect(jobStateFromError(new ApiError('conflict', 409, { jobState: 'processing' })))
      .toBe('processing');
  });

  it('ignores a value the client does not know', () => {
    expect(jobStateOf({ jobState: 'exploding' })).toBeUndefined();
    expect(jobStateFromError(new Error('network'))).toBeUndefined();
  });
});

describe('applyEpisodeJobState', () => {
  it('writes the reported state into the detail, list, and feed caches', () => {
    const client = makeClient();
    client.setQueryData(['episode', 'show', 'ep-1'], { id: 'ep-1', jobState: 'idle' });
    client.setQueryData(['episodes', 'show', 1], {
      episodes: [{ id: 'ep-1', jobState: 'idle' }, { id: 'ep-2', jobState: 'idle' }],
    });
    client.setQueryData(['feeds'], {
      feeds: [{ slug: 'show', latestEpisodes: [{ id: 'ep-1', jobState: 'idle' }] }],
    });

    applyEpisodeJobState(client, 'show', ['ep-1'], 'queued');

    expect(client.getQueryData(['episode', 'show', 'ep-1'])).toMatchObject({ jobState: 'queued' });
    expect(client.getQueryData(['episodes', 'show', 1])).toMatchObject({
      episodes: [{ id: 'ep-1', jobState: 'queued' }, { id: 'ep-2', jobState: 'idle' }],
    });
    expect(client.getQueryData(['feeds'])).toMatchObject({
      feeds: [{ slug: 'show', latestEpisodes: [{ id: 'ep-1', jobState: 'queued' }] }],
    });
  });

  it('leaves another feed untouched', () => {
    const client = makeClient();
    client.setQueryData(['feeds'], {
      feeds: [
        { slug: 'show', latestEpisodes: [{ id: 'ep-1', jobState: 'idle' }] },
        { slug: 'other', latestEpisodes: [{ id: 'ep-1', jobState: 'idle' }] },
      ],
    });

    applyEpisodeJobState(client, 'show', ['ep-1'], 'processing');

    expect(client.getQueryData(['feeds'])).toMatchObject({
      feeds: [
        { slug: 'show', latestEpisodes: [{ id: 'ep-1', jobState: 'processing' }] },
        { slug: 'other', latestEpisodes: [{ id: 'ep-1', jobState: 'idle' }] },
      ],
    });
  });

  it('does nothing without a state or without ids', () => {
    const client = makeClient();
    client.setQueryData(['episode', 'show', 'ep-1'], { id: 'ep-1', jobState: 'idle' });

    applyEpisodeJobState(client, 'show', ['ep-1'], undefined);
    applyEpisodeJobState(client, 'show', [], 'queued');

    expect(client.getQueryData(['episode', 'show', 'ep-1'])).toMatchObject({ jobState: 'idle' });
  });

  it('makes an applied state block the shared eligibility check', () => {
    const client = makeClient();
    client.setQueryData(['episode', 'show', 'ep-1'], { id: 'ep-1', jobState: 'idle' });
    expect(isActionBlocked('idle', false)).toBe(false);

    applyEpisodeJobState(client, 'show', ['ep-1'], 'queued');

    const detail = client.getQueryData<{ jobState: 'idle' | 'queued' }>(['episode', 'show', 'ep-1']);
    expect(isActionBlocked(detail?.jobState, false)).toBe(true);
  });
});
