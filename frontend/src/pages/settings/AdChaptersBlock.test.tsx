/**
 * Tests for the ad chapters block under the Generate Chapters toggle.
 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AdChaptersBlock, { type AdChaptersBlockProps } from './AdChaptersBlock';

function props(over: Partial<AdChaptersBlockProps> = {}): AdChaptersBlockProps {
  return {
    chaptersEnabled: true,
    enabled: true,
    categories: {
      sponsor: true, cross_promo: true, self_promo: false, interaction: false,
      intro: false, outro: false, recap: false,
    },
    includeHeld: false,
    titleFormat: '[mp:{category}]',
    heldTitleFormat: '[mp:{category}?]',
    resumeTitle: 'Show',
    minConfidence: 0.9,
    onEnabledChange: vi.fn(),
    onCategoryChange: vi.fn(),
    onIncludeHeldChange: vi.fn(),
    onTitleFormatChange: vi.fn(),
    onHeldTitleFormatChange: vi.fn(),
    onResumeTitleChange: vi.fn(),
    onMinConfidenceChange: vi.fn(),
    ...over,
  };
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
  it('hides the detail fields until ad chapters are on', () => {
    render(<AdChaptersBlock {...props({ enabled: false })} />);
    expect(screen.getByLabelText('Ad chapters')).toBeDefined();
    expect(screen.queryByLabelText('Chapter title')).toBeNull();
  });

  it('shows every category with the current checks', () => {
    render(<AdChaptersBlock {...props()} />);
    expect((screen.getByLabelText('Sponsor') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('Recap') as HTMLInputElement).checked).toBe(false);
  });

  it('reports a category toggle', async () => {
    const p = props();
    render(<AdChaptersBlock {...p} />);
    await userEvent.setup().click(screen.getByLabelText('Recap'));
    expect(p.onCategoryChange).toHaveBeenCalledWith('recap', true);
  });

  it('reports text and number edits', async () => {
    const p = props();
    const user = userEvent.setup();
    render(<Stateful {...p} />);
    await user.clear(screen.getByLabelText('Resume title'));
    await user.type(screen.getByLabelText('Resume title'), 'Back');
    expect(p.onResumeTitleChange).toHaveBeenLastCalledWith('Back');
    await user.clear(screen.getByLabelText('Minimum confidence'));
    await user.type(screen.getByLabelText('Minimum confidence'), '0.5');
    expect(p.onMinConfidenceChange).toHaveBeenLastCalledWith(0.5);
  });

  it('is disabled with a hint when chapter generation is off', () => {
    render(<AdChaptersBlock {...props({ chaptersEnabled: false })} />);
    expect(screen.getByText('Turn on Generate Chapters to use ad chapters.')).toBeDefined();
    // ToggleSwitch is a div with role=switch, so it carries the disabled
    // styling rather than the disabled property.
    expect(screen.getByLabelText('Ad chapters').className).toContain('cursor-not-allowed');
    expect((screen.getByLabelText('Sponsor') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Chapter title') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Minimum confidence') as HTMLInputElement).disabled).toBe(true);
  });
});
