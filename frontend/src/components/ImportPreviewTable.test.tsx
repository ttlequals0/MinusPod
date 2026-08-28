/**
 * Component tests for ImportPreviewTable.tsx.
 *
 * Covers:
 *   - An entry with errors renders the first error as visible text under
 *     the title, in both the desktop table and the sm:hidden mobile card
 *     (jsdom renders both regardless of the sm:hidden class, same as
 *     LocalFeedPanel.test.tsx's table-scoped assertions already account for).
 *   - An entry with warnings (but no errors) renders the first warning the
 *     same way.
 *   - A clean entry (no errors, no warnings) renders no reason line.
 *   - The status pill itself is unaffected (still just "ok"/"warning"/"error").
 */
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import ImportPreviewTable from './ImportPreviewTable';
import type { ImportPlanEntry } from '../api/feeds';

function makeEntry(overrides: Partial<ImportPlanEntry> = {}): ImportPlanEntry {
  return {
    episodeId: 's01e01',
    season: 1,
    episode: 1,
    title: 'The Beginning',
    audioFile: 'S01E01 - The Beginning.mp3',
    descriptionFile: null,
    artworkFile: null,
    sidecarFile: null,
    publishedAt: '2026-01-01T00:00:00Z',
    publishedAtSource: 'explicit',
    bytes: 1024,
    mtimeNs: 0,
    warnings: [],
    errors: [],
    replacesExisting: false,
    ...overrides,
  };
}

const EMPTY_TOTALS = { importable: 0, rejected: 0, errors: 0, bytes: 0 };

describe('ImportPreviewTable', () => {
  it('renders the first error as visible text under the title', () => {
    const entry = makeEntry({ errors: ['episode s01e01 already exists'] });
    const { container } = render(
      <ImportPreviewTable entries={[entry]} rejected={[]} totals={EMPTY_TOTALS} />,
    );

    const table = within(container.querySelector('table') as HTMLTableElement);
    expect(table.getByText('episode s01e01 already exists')).toBeDefined();
  });

  it('renders the first warning when there are no errors', () => {
    const entry = makeEntry({ warnings: ["sidecar artwork 'x.jpg' is invalid; fell back to embedded artwork"] });
    const { container } = render(
      <ImportPreviewTable entries={[entry]} rejected={[]} totals={EMPTY_TOTALS} />,
    );

    const table = within(container.querySelector('table') as HTMLTableElement);
    expect(table.getByText(/sidecar artwork 'x\.jpg' is invalid/)).toBeDefined();
  });

  it('prefers the error over a warning when an entry has both', () => {
    const entry = makeEntry({
      errors: ['episode s01e01 already exists'],
      warnings: ['some warning'],
    });
    render(<ImportPreviewTable entries={[entry]} rejected={[]} totals={EMPTY_TOTALS} />);

    expect(screen.getAllByText('episode s01e01 already exists').length).toBeGreaterThan(0);
    expect(screen.queryByText('some warning')).toBeNull();
  });

  it('renders no reason line for a clean entry', () => {
    const entry = makeEntry();
    const { container } = render(
      <ImportPreviewTable entries={[entry]} rejected={[]} totals={EMPTY_TOTALS} />,
    );

    // "ok" status pill still renders; no error/warning text anywhere.
    expect(screen.getAllByText('ok').length).toBeGreaterThan(0);
    expect(container.querySelector('.text-destructive')).toBeNull();
  });

  it('keeps the status pill as a plain status word, not the reason', () => {
    const entry = makeEntry({ errors: ['episode s01e01 already exists'] });
    render(<ImportPreviewTable entries={[entry]} rejected={[]} totals={EMPTY_TOTALS} />);

    expect(screen.getAllByText('error').length).toBeGreaterThan(0);
  });

  it('renders a batchErrors banner above the table', () => {
    const entry = makeEntry();
    render(
      <ImportPreviewTable
        entries={[entry]}
        rejected={[]}
        totals={EMPTY_TOTALS}
        batchErrors={['publish dates out of order: s01e02 must be before s01e01']}
      />,
    );

    expect(
      screen.getByText('publish dates out of order: s01e02 must be before s01e01'),
    ).toBeDefined();
    expect(screen.getByRole('alert')).toBeDefined();
  });

  it('renders no banner when batchErrors is empty or omitted', () => {
    const entry = makeEntry();
    render(<ImportPreviewTable entries={[entry]} rejected={[]} totals={EMPTY_TOTALS} batchErrors={[]} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
