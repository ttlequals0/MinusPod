/**
 * Tests for the Ad Detection settings section: the existing confidence/gap
 * controls plus the detection-tuning tunables added in 2.76.0 (verification
 * pass hold/autocut floors, pattern-learning floors, differential detection
 * thresholds). Follows NotificationsSection.test.tsx conventions.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AdDetectionSection from './AdDetectionSection';

interface TunablesState {
  minCutConfidence: number;
  minContentBetweenAdsSeconds: number;
  verificationMissHoldMinConfidence: number;
  verificationMissAutocutMinConfidence: number;
  learningMinConfidence: number;
  learningMinConfidenceLong: number;
  differentialMeasuredCorrMax: number;
  differentialHoldMinSeconds: number;
}

function defaultState(): TunablesState {
  return {
    minCutConfidence: 0.75,
    minContentBetweenAdsSeconds: 12,
    verificationMissHoldMinConfidence: 0.6,
    verificationMissAutocutMinConfidence: 0,
    learningMinConfidence: 0.85,
    learningMinConfidenceLong: 0.92,
    differentialMeasuredCorrMax: 0.6,
    differentialHoldMinSeconds: 10,
  };
}

// Mirrors how Settings.tsx wires this section: controlled state lifted to
// the parent, with a single Commit action standing in for the page's
// batched "Save Changes" button (computeChangedFields -> updateSettings).
function Harness({ onCommit }: { onCommit: (payload: TunablesState) => void }) {
  const [state, setState] = useState<TunablesState>(defaultState());
  const patch = <K extends keyof TunablesState>(key: K) => (v: TunablesState[K]) =>
    setState((s) => ({ ...s, [key]: v }));
  return (
    <>
      <AdDetectionSection
        minCutConfidence={state.minCutConfidence}
        onMinCutConfidenceChange={patch('minCutConfidence')}
        minContentBetweenAdsSeconds={state.minContentBetweenAdsSeconds}
        onMinContentBetweenAdsSecondsChange={patch('minContentBetweenAdsSeconds')}
        verificationMissHoldMinConfidence={state.verificationMissHoldMinConfidence}
        onVerificationMissHoldMinConfidenceChange={patch('verificationMissHoldMinConfidence')}
        verificationMissAutocutMinConfidence={state.verificationMissAutocutMinConfidence}
        onVerificationMissAutocutMinConfidenceChange={patch('verificationMissAutocutMinConfidence')}
        learningMinConfidence={state.learningMinConfidence}
        onLearningMinConfidenceChange={patch('learningMinConfidence')}
        learningMinConfidenceLong={state.learningMinConfidenceLong}
        onLearningMinConfidenceLongChange={patch('learningMinConfidenceLong')}
        differentialMeasuredCorrMax={state.differentialMeasuredCorrMax}
        onDifferentialMeasuredCorrMaxChange={patch('differentialMeasuredCorrMax')}
        differentialHoldMinSeconds={state.differentialHoldMinSeconds}
        onDifferentialHoldMinSecondsChange={patch('differentialHoldMinSeconds')}
      />
      <button onClick={() => onCommit(state)}>Commit</button>
    </>
  );
}

describe('AdDetectionSection: tunables render with defaults', () => {
  it('shows every new tunable at its default value', () => {
    render(<Harness onCommit={() => {}} />);
    expect((screen.getByLabelText('Hold floor') as HTMLInputElement).value).toBe('0.6');
    expect((screen.getByLabelText('Pattern-learning floor') as HTMLInputElement).value).toBe('0.85');
    expect((screen.getByLabelText('Pattern-learning floor, long ads') as HTMLInputElement).value).toBe('0.92');
    expect((screen.getByLabelText('Correlation ceiling') as HTMLInputElement).value).toBe('0.6');
    expect((screen.getByLabelText('Hold minimum length (s)') as HTMLInputElement).value).toBe('10');
  });

  it('renders the autocut toggle off by default and hides the autocut floor field', () => {
    render(<Harness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Enable verification autocut' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByLabelText('Autocut floor')).toBeNull();
  });

  it('does not show the "Disabled" badge when the differential hold minimum is non-zero', () => {
    render(<Harness onCommit={() => {}} />);
    expect(screen.queryByText('Disabled')).toBeNull();
  });
});

describe('AdDetectionSection: autocut toggle', () => {
  it('reveals the autocut floor at 0.5 when switched on, and clears it back to 0 when switched off', async () => {
    render(<Harness onCommit={() => {}} />);
    const user = userEvent.setup();
    const toggle = screen.getByRole('switch', { name: 'Enable verification autocut' });
    await user.click(toggle);
    expect((screen.getByLabelText('Autocut floor') as HTMLInputElement).value).toBe('0.5');
    await user.click(toggle);
    expect(screen.queryByLabelText('Autocut floor')).toBeNull();
  });
});

describe('AdDetectionSection: differential hold minimum "Disabled" badge', () => {
  it('shows "Disabled" once the field is cleared to 0', async () => {
    render(<Harness onCommit={() => {}} />);
    const user = userEvent.setup();
    const input = screen.getByLabelText('Hold minimum length (s)');
    await user.clear(input);
    await user.type(input, '0');
    expect(screen.getByText('Disabled')).toBeDefined();
  });
});

describe('AdDetectionSection: commit fires the batched save payload with camelCase keys', () => {
  it('produces an UpdateSettingsPayload-shaped object after editing every new tunable', async () => {
    let committed: TunablesState | null = null;
    render(<Harness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    const holdFloor = screen.getByLabelText('Hold floor');
    await user.clear(holdFloor);
    await user.type(holdFloor, '0.7');
    holdFloor.blur();

    await user.click(screen.getByRole('switch', { name: 'Enable verification autocut' }));
    const autocutFloor = screen.getByLabelText('Autocut floor');
    await user.clear(autocutFloor);
    await user.type(autocutFloor, '0.8');
    autocutFloor.blur();

    const learningFloor = screen.getByLabelText('Pattern-learning floor');
    await user.clear(learningFloor);
    await user.type(learningFloor, '0.9');
    learningFloor.blur();

    const learningFloorLong = screen.getByLabelText('Pattern-learning floor, long ads');
    await user.clear(learningFloorLong);
    await user.type(learningFloorLong, '0.95');
    learningFloorLong.blur();

    const corrMax = screen.getByLabelText('Correlation ceiling');
    await user.clear(corrMax);
    await user.type(corrMax, '0.4');
    corrMax.blur();

    const holdMinSeconds = screen.getByLabelText('Hold minimum length (s)');
    await user.clear(holdMinSeconds);
    await user.type(holdMinSeconds, '20');
    holdMinSeconds.blur();

    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({
      minCutConfidence: 0.75,
      minContentBetweenAdsSeconds: 12,
      verificationMissHoldMinConfidence: 0.7,
      verificationMissAutocutMinConfidence: 0.8,
      learningMinConfidence: 0.9,
      learningMinConfidenceLong: 0.95,
      differentialMeasuredCorrMax: 0.4,
      differentialHoldMinSeconds: 20,
    });
  });
});
