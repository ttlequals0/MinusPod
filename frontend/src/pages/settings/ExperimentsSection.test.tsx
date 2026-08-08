/**
 * Tests for per-prompt reset wiring in ExperimentsSection (issue #626): the
 * review and resurrect prompt fields each get their own two-click reset
 * button wired to a distinct callback. The bulk
 * "Reset Reviewer Prompts to Default" button is unaffected.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ExperimentsSection, { type ReviewerState } from './ExperimentsSection';

function baseReviewer(): ReviewerState {
  return {
    enabled: false,
    model: 'same_as_pass',
    maxShift: 60,
    reviewPrompt: 'review text',
    resurrectPrompt: 'resurrect text',
    reviewPromptOverride: '',
    resurrectPromptOverride: '',
    parallelAds: 4,
    updatePatterns: true,
    minTrimThreshold: 20,
  };
}

describe('ExperimentsSection: per-prompt reset', () => {
  it('hides both per-field reset buttons when both prompts are at their default', () => {
    render(
      <ExperimentsSection
        reviewer={baseReviewer()}
        onChange={vi.fn()}
        onResetPrompts={vi.fn()}
        resetIsPending={false}
        reviewPromptIsDefault
        resurrectPromptIsDefault
        onResetReviewPrompt={vi.fn()}
        onResetResurrectPrompt={vi.fn()}
      />,
    );
    expect(screen.queryAllByRole('button', { name: 'Reset' })).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Reset Reviewer Prompts to Default' })).toBeDefined();
  });

  it('fires resetPrompt(review) only from the review field', async () => {
    const onResetReviewPrompt = vi.fn();
    const onResetResurrectPrompt = vi.fn();
    const user = userEvent.setup();
    render(
      <ExperimentsSection
        reviewer={baseReviewer()}
        onChange={vi.fn()}
        onResetPrompts={vi.fn()}
        resetIsPending={false}
        reviewPromptIsDefault={false}
        resurrectPromptIsDefault
        onResetReviewPrompt={onResetReviewPrompt}
        onResetResurrectPrompt={onResetResurrectPrompt}
      />,
    );
    const [resetBtn] = screen.getAllByRole('button', { name: 'Reset' });
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(onResetReviewPrompt).toHaveBeenCalledTimes(1);
    expect(onResetResurrectPrompt).not.toHaveBeenCalled();
  });

  it('fires resetPrompt(resurrect) only from the resurrect field', async () => {
    const onResetReviewPrompt = vi.fn();
    const onResetResurrectPrompt = vi.fn();
    const user = userEvent.setup();
    render(
      <ExperimentsSection
        reviewer={baseReviewer()}
        onChange={vi.fn()}
        onResetPrompts={vi.fn()}
        resetIsPending={false}
        reviewPromptIsDefault
        resurrectPromptIsDefault={false}
        onResetReviewPrompt={onResetReviewPrompt}
        onResetResurrectPrompt={onResetResurrectPrompt}
      />,
    );
    const [resetBtn] = screen.getAllByRole('button', { name: 'Reset' });
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(onResetResurrectPrompt).toHaveBeenCalledTimes(1);
    expect(onResetReviewPrompt).not.toHaveBeenCalled();
  });
});
