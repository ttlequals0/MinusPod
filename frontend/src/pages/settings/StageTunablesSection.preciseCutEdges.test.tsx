/**
 * The hidden "Precise cut edges" switch in the LLM tunables section.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StageTunablesSection from './StageTunablesSection';
import type { UpdateSettingsPayload } from '../../api/types';
import { baseDefaults, baseTunables } from './tunablesTestFixtures';

vi.mock('../../hooks/useLlmEnabled', async () => (await import('../../test/llmEnabled')).llmEnabledMock);

function Harness({
  preciseCutEdges,
  onSave = () => {},
}: {
  preciseCutEdges?: boolean;
  onSave?: (payload: UpdateSettingsPayload) => void;
}) {
  return (
    <StageTunablesSection
      tunables={baseTunables}
      defaults={baseDefaults}
      llmProvider="openai-compatible"
      detectionProvider=""
      verificationProvider=""
      chaptersProvider=""
      reviewProvider=""
      onSave={onSave}
      saveIsPending={false}
      saveIsSuccess={false}
      saveError={null}
      parallelWindows={4}
      parallelWindowsDefault={4}
      omitTemperature={false}
      preciseCutEdges={preciseCutEdges}
    />
  );
}

describe('StageTunablesSection: Precise cut edges switch', () => {
  it('is off when the prop is absent', () => {
    render(<Harness />);
    expect(screen.getByRole('switch', { name: 'Precise cut edges' }).getAttribute('aria-checked')).toBe('false');
  });

  it('reflects the saved value', () => {
    render(<Harness preciseCutEdges />);
    expect(screen.getByRole('switch', { name: 'Precise cut edges' }).getAttribute('aria-checked')).toBe('true');
  });

  it('sends only { preciseCutEdges: true } after switching on and saving', async () => {
    let saved: UpdateSettingsPayload | null = null;
    render(<Harness onSave={(payload) => { saved = payload; }} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('switch', { name: 'Precise cut edges' }));
    await user.click(screen.getByRole('button', { name: 'Save LLM tunables' }));
    expect(saved).toEqual({ preciseCutEdges: true });
  });
});
