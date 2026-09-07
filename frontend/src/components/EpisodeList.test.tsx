import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import EpisodeList from './EpisodeList';
import type { Episode } from '../api/types';

describe('EpisodeList', () => {
  it('links a recents row to its source feed and names it', () => {
    const episode = {
      id: 'ep1', title: 'From alpha', published: '2026-09-10T00:00:00Z', status: 'completed',
      feedSlug: 'alpha', feedTitle: 'Alpha Show',
    } as Episode;
    render(<MemoryRouter><EpisodeList feedSlug="recents" episodes={[episode]} /></MemoryRouter>);
    expect(screen.getByRole('link', { name: /From alpha/ }).getAttribute('href')).toBe('/feeds/alpha/episodes/ep1');
    expect(screen.getByText('Alpha Show')).toBeTruthy();
  });

  it('links an ordinary row within its own feed', () => {
    const episode = { id: 'ep2', title: 'Own', published: '2026-09-10T00:00:00Z', status: 'completed' } as Episode;
    render(<MemoryRouter><EpisodeList feedSlug="show" episodes={[episode]} /></MemoryRouter>);
    expect(screen.getByRole('link', { name: /Own/ }).getAttribute('href')).toBe('/feeds/show/episodes/ep2');
  });
});
