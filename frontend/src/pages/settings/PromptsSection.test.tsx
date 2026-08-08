/**
 * Tests for per-prompt reset wiring in PromptsSection (#626): each base
 * prompt field gets its own two-click reset button; the bulk button is
 * unaffected.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PromptsSection from './PromptsSection';

function baseProps() {
  return {
    systemPrompt: 'system text',
    verificationPrompt: 'verification text',
    chapterPrompt: 'chapter text',
    systemPromptOverride: '',
    verificationPromptOverride: '',
    chapterPromptOverride: '',
    onSystemPromptChange: vi.fn(),
    onVerificationPromptChange: vi.fn(),
    onChapterPromptChange: vi.fn(),
    onSystemPromptOverrideChange: vi.fn(),
    onVerificationPromptOverrideChange: vi.fn(),
    onChapterPromptOverrideChange: vi.fn(),
    onResetPrompts: vi.fn(),
    resetIsPending: false,
  };
}

describe('PromptsSection: per-prompt reset', () => {
  it('renders all per-field reset buttons disabled when every prompt is at its default', () => {
    render(
      <PromptsSection
        {...baseProps()}
        systemPromptIsDefault
        verificationPromptIsDefault
        chapterPromptIsDefault
        onResetSystemPrompt={vi.fn()}
        onResetVerificationPrompt={vi.fn()}
        onResetChapterPrompt={vi.fn()}
      />,
    );
    const resetButtons = screen.getAllByRole('button', { name: 'Reset' });
    expect(resetButtons).toHaveLength(3);
    for (const btn of resetButtons) expect(btn).toHaveProperty('disabled', true);
    // Bulk reset button stays regardless of per-field state.
    expect(screen.getByRole('button', { name: 'Reset Prompts to Default' })).toBeDefined();
  });

  it('fires the system-prompt callback for the first field only', async () => {
    const onResetSystemPrompt = vi.fn();
    const onResetVerificationPrompt = vi.fn();
    const onResetChapterPrompt = vi.fn();
    const user = userEvent.setup();
    render(
      <PromptsSection
        {...baseProps()}
        systemPromptIsDefault={false}
        verificationPromptIsDefault
        chapterPromptIsDefault
        onResetSystemPrompt={onResetSystemPrompt}
        onResetVerificationPrompt={onResetVerificationPrompt}
        onResetChapterPrompt={onResetChapterPrompt}
      />,
    );
    const [resetBtn] = screen.getAllByRole('button', { name: 'Reset' });
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(onResetSystemPrompt).toHaveBeenCalledTimes(1);
    expect(onResetVerificationPrompt).not.toHaveBeenCalled();
    expect(onResetChapterPrompt).not.toHaveBeenCalled();
  });

  it('fires the chapter-prompt callback for the third field only', async () => {
    const onResetSystemPrompt = vi.fn();
    const onResetVerificationPrompt = vi.fn();
    const onResetChapterPrompt = vi.fn();
    const user = userEvent.setup();
    render(
      <PromptsSection
        {...baseProps()}
        systemPromptIsDefault
        verificationPromptIsDefault
        chapterPromptIsDefault={false}
        onResetSystemPrompt={onResetSystemPrompt}
        onResetVerificationPrompt={onResetVerificationPrompt}
        onResetChapterPrompt={onResetChapterPrompt}
      />,
    );
    const resetBtn = screen.getAllByRole('button', { name: 'Reset' })[2];
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(onResetChapterPrompt).toHaveBeenCalledTimes(1);
    expect(onResetSystemPrompt).not.toHaveBeenCalled();
    expect(onResetVerificationPrompt).not.toHaveBeenCalled();
  });
});
