import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AudioCueDetectionSection, { type AudioCueState } from './AudioCueDetectionSection';

const state: AudioCueState = {
  enabled: true,
  freqMinHz: 1500,
  freqMaxHz: 8000,
  prominenceDb: 9,
  minConfidence: 0.8,
  templateScore: 0.75,
  formantAttenDb: 0,
  createFromPairs: false,
  cutMatches: false,
  snapConfidence: 0.8,
  snapLeadSeconds: 10,
  snapLagSeconds: 4,
  captureMinSeconds: 0.2,
  captureMaxSeconds: 10,
  captureMaxIntroSeconds: 60,
  captureMaxOutroSeconds: 60,
  pairConfidence: 0.9,
  pairMinBreakSeconds: 30,
  pairMaxBreakSeconds: 480,
  pairMaxBreakFraction: 0.5,
  silenceSnapNoiseDb: -40,
  silenceSnapMinDurationSeconds: 0.2,
  silenceSnapMaxDistanceSeconds: 2,
};

describe('AudioCueDetectionSection cut matched cue audio', () => {
  it('toggles cutMatches through onChange', async () => {
    const onChange = vi.fn();
    render(<AudioCueDetectionSection audioCue={state} onChange={onChange} />);
    const sw = await screen.findByRole('switch', { name: 'Cut matched cue audio' });
    expect(sw.getAttribute('aria-checked')).toBe('false');
    await userEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith({ ...state, cutMatches: true });
  });
});
