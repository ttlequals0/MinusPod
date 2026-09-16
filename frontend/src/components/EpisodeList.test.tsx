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

  it('does not offer a select checkbox for a row with jobState queued', () => {
    const episode = {
      id: 'ep3', title: 'Queued one', published: '2026-09-10T00:00:00Z', status: 'completed', jobState: 'queued',
    } as Episode;
    render(
      <MemoryRouter>
        <EpisodeList feedSlug="show" episodes={[episode]} selectedIds={new Set()} onToggle={() => {}} />
      </MemoryRouter>
    );
    expect(screen.queryByLabelText('Select episode')).toBeNull();
  });

  it('leaves a sibling idle row selectable while another row is queued', () => {
    const queued = {
      id: 'ep4', title: 'Queued', published: '2026-09-10T00:00:00Z', status: 'completed', jobState: 'queued',
    } as Episode;
    const idle = {
      id: 'ep5', title: 'Idle', published: '2026-09-10T00:00:00Z', status: 'completed', jobState: 'idle',
    } as Episode;
    render(
      <MemoryRouter>
        <EpisodeList feedSlug="show" episodes={[queued, idle]} selectedIds={new Set()} onToggle={() => {}} />
      </MemoryRouter>
    );
    expect(screen.getAllByLabelText('Select episode')).toHaveLength(1);
  });

  it('offers a select checkbox for a row with jobState idle', () => {
    const episode = {
      id: 'ep6', title: 'Idle one', published: '2026-09-10T00:00:00Z', status: 'completed', jobState: 'idle',
    } as Episode;
    render(
      <MemoryRouter>
        <EpisodeList feedSlug="show" episodes={[episode]} selectedIds={new Set()} onToggle={() => {}} />
      </MemoryRouter>
    );
    expect(screen.getByLabelText('Select episode')).toBeTruthy();
  });

  it('shows a Pass-through chip when passthroughEnabled is true', () => {
    const episode = {
      id: 'ep7', title: 'Relayed', published: '2026-09-10T00:00:00Z', status: 'completed', passthroughEnabled: true,
    } as Episode;
    render(<MemoryRouter><EpisodeList feedSlug="show" episodes={[episode]} /></MemoryRouter>);
    expect(screen.getByText('Pass-through')).toBeTruthy();
  });

  it('omits the Pass-through chip when passthroughEnabled is falsy', () => {
    const episode = {
      id: 'ep8', title: 'Normal', published: '2026-09-10T00:00:00Z', status: 'completed',
    } as Episode;
    render(<MemoryRouter><EpisodeList feedSlug="show" episodes={[episode]} /></MemoryRouter>);
    expect(screen.queryByText('Pass-through')).toBeNull();
  });
});
