/**
 * Tests for the ad chapters block under the Generate Chapters toggle.
 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import userEvent from '@testing-library/user-event';
import AdChaptersBlock, { type AdChaptersBlockProps } from './AdChaptersBlock';

function props(over: Partial<AdChaptersBlockProps> = {}): AdChaptersBlockProps {
  return {
    chaptersEnabled: true,
    includeHeld: false,
    titleFormat: '[mp:{category}]',
    heldTitleFormat: '[mp:{category}?]',
    resumeTitle: 'Show',
    minConfidence: 0.9,
    onIncludeHeldChange: vi.fn(),
    onTitleFormatChange: vi.fn(),
    onHeldTitleFormatChange: vi.fn(),
    onResumeTitleChange: vi.fn(),
    onMinConfidenceChange: vi.fn(),
    ...over,
  };
}

function renderBlock(p: AdChaptersBlockProps) {
  return render(<MemoryRouter><AdChaptersBlock {...p} /></MemoryRouter>);
}

// The inputs are controlled by the parent, so an edit test needs a parent that
// feeds the new value back in, as Settings does.
function Stateful(p: AdChaptersBlockProps) {
  const [resumeTitle, setResumeTitle] = useState(p.resumeTitle);
  const [minConfidence, setMinConfidence] = useState(p.minConfidence);
  return (
    <AdChaptersBlock
      {...p}
      resumeTitle={resumeTitle}
      minConfidence={minConfidence}
      onResumeTitleChange={(v) => { setResumeTitle(v); p.onResumeTitleChange(v); }}
      onMinConfidenceChange={(v) => { setMinConfidence(v); p.onMinConfidenceChange(v); }}
    />
  );
}

describe('AdChaptersBlock', () => {
  it('has no enable toggle or category checkbox matrix', () => {
    renderBlock(props());
    expect(screen.queryByLabelText('Ad chapters')).toBeNull();
    expect(screen.queryByText('Chapter these categories')).toBeNull();
    expect(screen.queryByLabelText('Sponsor')).toBeNull();
    expect(screen.queryByLabelText('Recap')).toBeNull();
  });

  it('shows the helper line linking to Segment actions', () => {
    renderBlock(props());
    expect(screen.getByText(/Set a category's action to Mark in/)).toBeDefined();
    const link = screen.getByRole('link', { name: 'Segment actions' });
    expect(link.getAttribute('href')).toBe('/#segment-actions');
  });

  it('shows title formats, resume title, and minimum confidence', () => {
    renderBlock(props());
    expect(screen.getByLabelText('Chapter title')).toBeDefined();
    expect(screen.getByLabelText('Title while waiting for review')).toBeDefined();
    expect(screen.getByLabelText('Resume title')).toBeDefined();
    expect(screen.getByLabelText('Minimum confidence')).toBeDefined();
  });

  it('reports text and number edits', async () => {
    const p = props();
    const user = userEvent.setup();
    render(<MemoryRouter><Stateful {...p} /></MemoryRouter>);
    await user.clear(screen.getByLabelText('Resume title'));
    await user.type(screen.getByLabelText('Resume title'), 'Back');
    expect(p.onResumeTitleChange).toHaveBeenLastCalledWith('Back');
    await user.clear(screen.getByLabelText('Minimum confidence'));
    await user.type(screen.getByLabelText('Minimum confidence'), '0.5');
    expect(p.onMinConfidenceChange).toHaveBeenLastCalledWith(0.5);
  });

  it('is disabled with a hint when chapter generation is off', () => {
    renderBlock(props({ chaptersEnabled: false }));
    expect(screen.getByText('Turn on Generate Chapters to use ad chapters.')).toBeDefined();
    expect((screen.getByLabelText('Chapter title') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Minimum confidence') as HTMLInputElement).disabled).toBe(true);
  });
});
