import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import ProcessingJobProgress from './ProcessingJobProgress';

const job = {
  slug: 'example-podcast',
  episodeId: 'a1b2c3d4e5f6',
  title: 'The Daily Tech Show',
  podcastName: 'Example',
  stage: 'pass1:transcribing',
  progress: 20,
};

function renderJob(extra: Partial<typeof job> & { stageDetail?: unknown }) {
  render(
    <MemoryRouter>
      <ProcessingJobProgress job={{ ...job, ...extra } as never} elapsed={192} />
    </MemoryRouter>,
  );
}

describe('ProcessingJobProgress stage detail', () => {
  it('shows seconds as minutes before the percent', () => {
    renderJob({ stageDetail: { processed: 894, total: 2706, unit: 'seconds' } });
    expect(screen.getByText('14.9 / 45.1 min - 20% - 3m 12s')).toBeTruthy();
  });

  it('shows chunk counts', () => {
    renderJob({ stageDetail: { processed: 3, total: 12, unit: 'chunks' } });
    expect(screen.getByText('3 / 12 chunks - 20% - 3m 12s')).toBeTruthy();
  });

  it('keeps the plain line when the detail is absent or has no total', () => {
    renderJob({});
    expect(screen.getByText('20% - 3m 12s')).toBeTruthy();
  });

  it('ignores a zero total', () => {
    renderJob({ stageDetail: { processed: 0, total: 0, unit: 'seconds' } });
    expect(screen.getByText('20% - 3m 12s')).toBeTruthy();
  });
});
