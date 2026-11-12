import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AudioFingerprintingSection, {
  FINGERPRINT_FALLBACK_LIMITS, fingerprintLimits, type FingerprintState,
} from './AudioFingerprintingSection';

const mockUnsaved = vi.fn();
vi.mock('../../hooks/useUnsavedChanges', () => ({
  useUnsavedChanges: (dirty: boolean) => mockUnsaved(dirty),
}));

const state: FingerprintState = {
  enabled: true,
  matchStrictness: 0.65,
  minCoverage: 0.5,
  gapToleranceS: 3.5,
  minAdLengthS: 5,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem('settings-section-audio-fingerprinting', 'true');
});

const onFieldIssue = vi.fn();

function renderSection(
  over: Partial<FingerprintState> = {}, onChange = vi.fn(), limits = fingerprintLimits(undefined),
) {
  render(
    <AudioFingerprintingSection
      fingerprint={{ ...state, ...over }}
      limits={limits}
      onChange={onChange}
      onFieldIssue={onFieldIssue}
    />,
  );
  return onChange;
}

describe('AudioFingerprintingSection', () => {
  it('shows the switch and four fields with their values, units and one line of help each', () => {
    renderSection();
    expect(screen.getByRole('switch', { name: 'Enable audio fingerprinting' }).getAttribute('aria-checked')).toBe('true');
    expect((screen.getByLabelText('Match strictness') as HTMLInputElement).value).toBe('0.65');
    expect((screen.getByLabelText('Minimum coverage') as HTMLInputElement).value).toBe('0.5');
    expect((screen.getByLabelText('Gap tolerance (s)') as HTMLInputElement).value).toBe('3.5');
    expect((screen.getByLabelText('Minimum ad length (s)') as HTMLInputElement).value).toBe('5');
    expect(screen.getByText('65% (65% to 95%)')).toBeDefined();
    expect(screen.getByText('50% (10% to 100%)')).toBeDefined();
    expect(screen.getByText('0 to 15')).toBeDefined();
    expect(screen.getByText('3 to 30')).toBeDefined();
    expect(screen.getByText(/Raising it requires a closer match/)).toBeDefined();
    expect(screen.getByText(/Raising it requires more of the stored ad/)).toBeDefined();
    expect(screen.getByText(/Raising it bridges longer dropouts/)).toBeDefined();
    expect(screen.getByText(/Raising it skips shorter spans/)).toBeDefined();
  });

  it('falls back to the typed limits when the response has none', () => {
    renderSection();
    expect(FINGERPRINT_FALLBACK_LIMITS.matchStrictness).toMatchObject({ min: 0.65, max: 0.95, default: 0.65 });
    expect(FINGERPRINT_FALLBACK_LIMITS.minCoverage).toMatchObject({ min: 0.1, max: 1, default: 0.5 });
    expect(FINGERPRINT_FALLBACK_LIMITS.gapToleranceS).toMatchObject({ min: 0, max: 15, default: 3.5 });
    expect(FINGERPRINT_FALLBACK_LIMITS.minAdLengthS).toMatchObject({ min: 3, max: 30, default: 5 });
    const input = screen.getByLabelText('Match strictness') as HTMLInputElement;
    expect(input.min).toBe('0.65');
    expect(input.max).toBe('0.95');
  });

  it('commits a valid entry and the switch through onChange', async () => {
    const onChange = renderSection();
    const input = screen.getByLabelText('Gap tolerance (s)');
    await userEvent.clear(input);
    await userEvent.type(input, '6');
    expect(onChange).toHaveBeenLastCalledWith({ ...state, gapToleranceS: 6 });
    await userEvent.click(screen.getByRole('switch', { name: 'Enable audio fingerprinting' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...state, enabled: false });
  });

  it('flags an out-of-range entry with the range, sends nothing, and clears once it is valid', async () => {
    const onChange = renderSection();
    const input = screen.getByLabelText('Match strictness') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '0.2');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const message = screen.getByText('Enter a number from 0.65 to 0.95.');
    expect(input.getAttribute('aria-describedby')).toContain(message.id);
    expect(onChange).not.toHaveBeenCalledWith(expect.objectContaining({ matchStrictness: 0.2 }));
    expect(mockUnsaved).toHaveBeenCalledWith(true);
    await userEvent.clear(input);
    await userEvent.type(input, '0.8');
    expect(input.getAttribute('aria-invalid')).toBeNull();
    expect(screen.queryByText('Enter a number from 0.65 to 0.95.')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith({ ...state, matchStrictness: 0.8 });
  });

  it('treats a cleared field as invalid instead of committing zero', async () => {
    const onChange = renderSection();
    const input = screen.getByLabelText('Minimum ad length (s)');
    await userEvent.clear(input);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Enter a number from 3 to 30.')).toBeDefined();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('takes min, max, step and default from the settings response', async () => {
    const served = {
      fingerprintMatchStrictness: { value: 0.7, isDefault: false, min: 0.7, max: 0.9, step: 0.01 },
      defaults: { fingerprintMatchStrictness: 0.8 },
    } as never;
    const limits = fingerprintLimits(served);
    expect(limits.matchStrictness).toEqual({ min: 0.7, max: 0.9, step: 0.01, default: 0.8 });
    expect(limits.minCoverage).toEqual(FINGERPRINT_FALLBACK_LIMITS.minCoverage);
    renderSection({ matchStrictness: 0.7 }, vi.fn(), limits);
    const input = screen.getByLabelText('Match strictness') as HTMLInputElement;
    expect([input.min, input.max, input.step]).toEqual(['0.7', '0.9', '0.01']);
    await userEvent.clear(input);
    await userEvent.type(input, '0.68');
    expect(screen.getByText('Enter a number from 0.7 to 0.9.')).toBeDefined();
  });

  it('reports an invalid entry with a focus callback and clears it when fixed', async () => {
    renderSection();
    const input = screen.getByLabelText('Match strictness') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '0.2');
    const [key, focus] = onFieldIssue.mock.calls[onFieldIssue.mock.calls.length - 1];
    expect(key).toBe('fingerprintMatchStrictness');
    (focus as () => void)();
    expect(document.activeElement).toBe(input);
    await userEvent.clear(input);
    await userEvent.type(input, '0.8');
    expect(onFieldIssue).toHaveBeenLastCalledWith('fingerprintMatchStrictness', null);
  });

  it('reports nothing for a stored value outside the range that was not typed', () => {
    renderSection({ matchStrictness: 0.3 });
    expect(onFieldIssue).not.toHaveBeenCalled();
    expect(mockUnsaved).not.toHaveBeenCalledWith(true);
  });

  it('registers no unsaved state while every entry is valid', () => {
    renderSection();
    expect(mockUnsaved).not.toHaveBeenCalledWith(true);
  });
});
