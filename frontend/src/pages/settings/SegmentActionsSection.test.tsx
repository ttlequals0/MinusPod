/**
 * Tests for the Segment actions settings card: the per-category
 * remove/beep/keep matrix (relocated from GlobalDefaultsSection) plus the
 * global detectShowSegments default added alongside it.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SegmentActionsSection from './SegmentActionsSection';

// Mirrors the "matrix PUT payload" saved immediately per row: the harness
// builds the same partial-map shape Settings.tsx sends through
// updateSettings({ segmentCategoryActions: { [category]: action } }).
function SegmentActionsHarness({ onCommit }: {
  onCommit: (payload: { segmentCategoryActions: Record<string, string> }) => void;
}) {
  const [actions, setActions] = useState<Record<string, string>>({});
  const [detectShowSegments, setDetectShowSegments] = useState(false);
  return (
    <SegmentActionsSection
      segmentCategoryActions={actions}
      onSegmentCategoryActionChange={(category, action) => {
        setActions((prev) => ({ ...prev, [category]: action }));
        onCommit({ segmentCategoryActions: { [category]: action } });
      }}
      detectShowSegments={detectShowSegments}
      onDetectShowSegmentsChange={setDetectShowSegments}
    />
  );
}

describe('SegmentActionsSection: category matrix', () => {
  it('renders all seven category rows with Remove selected by default', () => {
    render(<SegmentActionsHarness onCommit={() => {}} />);
    for (const label of ['Sponsor', 'Cross-promo', 'Self-promo', 'Interaction', 'Intro', 'Outro', 'Recap']) {
      const group = screen.getByRole('radiogroup', { name: `${label} action` });
      const removeBtn = within(group).getByRole('radio', { name: 'Remove' });
      expect(removeBtn.getAttribute('aria-checked')).toBe('true');
    }
  });

  it('clicking Keep on Cross-promo commits { segmentCategoryActions: { cross_promo: "keep" } }', async () => {
    let committed: { segmentCategoryActions: Record<string, string> } | null = null;
    render(<SegmentActionsHarness onCommit={(payload) => { committed = payload; }} />);

    const group = screen.getByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(within(group).getByRole('radio', { name: 'Keep' }));

    expect(committed).toEqual({ segmentCategoryActions: { cross_promo: 'keep' } });
  });

  it('clicking Beep on Intro commits { segmentCategoryActions: { intro: "beep" } } and leaves other rows untouched', async () => {
    const commits: Array<{ segmentCategoryActions: Record<string, string> }> = [];
    render(<SegmentActionsHarness onCommit={(payload) => commits.push(payload)} />);

    const introGroup = screen.getByRole('radiogroup', { name: 'Intro action' });
    await userEvent.click(within(introGroup).getByRole('radio', { name: 'Beep' }));

    expect(commits).toEqual([{ segmentCategoryActions: { intro: 'beep' } }]);
    const sponsorGroup = screen.getByRole('radiogroup', { name: 'Sponsor action' });
    expect(within(sponsorGroup).getByRole('radio', { name: 'Remove' }).getAttribute('aria-checked')).toBe('true');
  });
});

describe('SegmentActionsSection: global show-segments default', () => {
  it('renders off by default', () => {
    render(<SegmentActionsHarness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Detect intro, outro, and housekeeping segments' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('fires its own setter, not the category mutation, when switched on', async () => {
    const commits: Array<{ segmentCategoryActions: Record<string, string> }> = [];
    render(<SegmentActionsHarness onCommit={(payload) => commits.push(payload)} />);

    const toggle = screen.getByRole('switch', { name: 'Detect intro, outro, and housekeeping segments' });
    await userEvent.click(toggle);

    expect(toggle.getAttribute('aria-checked')).toBe('true');
    expect(commits).toEqual([]);
  });
});
