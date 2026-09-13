/**
 * Tests for per-prompt reset wiring in AdReviewerSection (#626): review and
 * resurrect each get their own two-click reset button; the bulk button is
 * unaffected.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AdReviewerSection, { type ReviewerState } from './AdReviewerSection';

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

function renderSection(props: Partial<Parameters<typeof AdReviewerSection>[0]> = {}) {
  return render(
    <AdReviewerSection
      reviewer={baseReviewer()}
      onChange={vi.fn()}
      onResetPrompts={vi.fn()}
      resetIsPending={false}
      onResetReviewPrompt={vi.fn()}
      onResetResurrectPrompt={vi.fn()}
      {...props}
    />,
  );
}

describe('AdReviewerSection: per-prompt reset', () => {
  it('renders both per-field reset buttons disabled when both prompts are at their default', () => {
    renderSection({ reviewPromptIsDefault: true, resurrectPromptIsDefault: true });
    const resetButtons = screen.getAllByRole('button', { name: 'Reset' });
    expect(resetButtons).toHaveLength(2);
    for (const btn of resetButtons) expect(btn).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Reset Reviewer Prompts to Default' })).toBeDefined();
  });

  it('fires resetPrompt(review) only from the review field', async () => {
    const onResetReviewPrompt = vi.fn();
    const onResetResurrectPrompt = vi.fn();
    const user = userEvent.setup();
    renderSection({
      reviewPromptIsDefault: false,
      resurrectPromptIsDefault: true,
      onResetReviewPrompt,
      onResetResurrectPrompt,
    });
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
    renderSection({
      reviewPromptIsDefault: true,
      resurrectPromptIsDefault: false,
      onResetReviewPrompt,
      onResetResurrectPrompt,
    });
    const resetBtn = screen.getAllByRole('button', { name: 'Reset' })[1];
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(onResetResurrectPrompt).toHaveBeenCalledTimes(1);
    expect(onResetReviewPrompt).not.toHaveBeenCalled();
  });
});

describe('AdReviewerSection: not framed as experimental', () => {
  it('carries no experimental badge', () => {
    renderSection();
    expect(screen.queryByText(/experimental/i)).toBeNull();
  });
});

describe('AdReviewerSection: review model select', () => {
  const options = [{ id: 'z-ai/glm-5.3-flash', label: 'GLM 5.3 Flash' }];

  it('shows an off-catalog stored model as the selected option', () => {
    renderSection({
      reviewer: { ...baseReviewer(), model: 'claude-opus-5' },
      modelOptions: options,
    });
    const select = screen.getByLabelText('Review model') as HTMLSelectElement;
    expect(select.value).toBe('claude-opus-5');
    expect(screen.getByRole('option', { name: /claude-opus-5 \(current, not in catalog\)/ })).toBeDefined();
  });

  it('keeps same_as_pass on the default option', () => {
    renderSection({ modelOptions: options });
    const select = screen.getByLabelText('Review model') as HTMLSelectElement;
    expect(select.value).toBe('same_as_pass');
    expect(select.selectedOptions[0].textContent).toBe('Same as pass model');
    expect(screen.queryByRole('option', { name: /not in catalog/ })).toBeNull();
  });

  it('adds no extra option when the stored model is in the catalog', () => {
    renderSection({
      reviewer: { ...baseReviewer(), model: 'z-ai/glm-5.3-flash' },
      modelOptions: options,
    });
    const select = screen.getByLabelText('Review model') as HTMLSelectElement;
    expect(select.value).toBe('z-ai/glm-5.3-flash');
    expect(screen.queryByRole('option', { name: /not in catalog/ })).toBeNull();
  });
});
