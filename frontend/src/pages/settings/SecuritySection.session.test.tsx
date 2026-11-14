import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SecuritySection from './SecuritySection';
import { ApiError } from '../../api/client';
import { hasUnsavedChanges } from '../../hooks/useUnsavedChanges';

const mockGetSettings = vi.fn();
const mockUpdateSettings = vi.fn();
vi.mock('../../api/settings', () => ({
  getSettings: () => mockGetSettings(),
  updateSettings: (p: unknown) => mockUpdateSettings(p),
}));
vi.mock('../../api/auth', () => ({ setPassword: vi.fn(), removePassword: vi.fn() }));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const renderSection = () =>
  wrap(<SecuritySection isPasswordSet logout={vi.fn()} refreshStatus={vi.fn() as never} />);
const saveButton = () => screen.getByRole('button', { name: /save session lengths|saving/i }) as HTMLButtonElement;

beforeEach(() => {
  mockGetSettings.mockReset(); mockUpdateSettings.mockReset();
  mockUpdateSettings.mockResolvedValue({ message: 'Settings updated' });
  mockGetSettings.mockResolvedValue({
    jitBlockedUserAgents: { value: [], isDefault: true },
    sessionLifetimeHours: { value: 24, isDefault: true },
    sessionRememberDays: { value: 30, isDefault: true },
  });
});

describe('Security session-length fields', () => {
  it('renders both labels with the current values and associated help', async () => {
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)') as HTMLInputElement;
    const days = screen.getByLabelText('Remember me length (days)') as HTMLInputElement;
    expect(hours.value).toBe('24');
    expect(days.value).toBe('30');
    expect(hours.getAttribute('aria-describedby')).toContain('sessionLifetimeHours-help');
    expect(days.getAttribute('aria-describedby')).toContain('sessionRememberDays-help');
  });

  it('saves both fields through updateSettings on Save', async () => {
    const user = userEvent.setup();
    renderSection();
    await user.click(screen.getByText('Security'));
    const hours = await screen.findByLabelText('Session length (hours)');
    await user.clear(hours);
    await user.type(hours, '48');
    await user.click(screen.getByRole('button', { name: 'Save session lengths' }));
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith(
      { sessionLifetimeHours: 48, sessionRememberDays: 30 }));
    await waitFor(() => expect(screen.queryByRole('status')?.textContent).toBe('Session lengths saved.'));
  });

  it('persists a typed value when Save is clicked without blurring the field', async () => {
    renderSection();
    const days = await screen.findByLabelText('Remember me length (days)');
    days.focus();
    fireEvent.change(days, { target: { value: '90' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith(
      { sessionLifetimeHours: 24, sessionRememberDays: 90 }));
  });

  it.each(['', '0', '8761', '1.5', 'e'])('blocks Save and flags the field for hours entry %j', async (entry) => {
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)');
    fireEvent.change(hours, { target: { value: entry } });
    expect(hours.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByRole('alert').textContent).toBe('Enter a whole number of hours from 1 to 8760.');
    expect(saveButton().disabled).toBe(true);
    fireEvent.submit(hours.closest('form') as HTMLFormElement);
    expect(mockUpdateSettings).not.toHaveBeenCalled();
  });

  it('takes the accepted range from the settings response', async () => {
    mockGetSettings.mockResolvedValue({
      jitBlockedUserAgents: { value: [], isDefault: true },
      sessionLifetimeHours: { value: 24, isDefault: true, min: 2, max: 100 },
      sessionRememberDays: { value: 30, isDefault: true, min: 1, max: 60 },
    });
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)') as HTMLInputElement;
    await waitFor(() => expect(hours.max).toBe('100'));
    expect(hours.min).toBe('2');
    fireEvent.change(hours, { target: { value: '101' } });
    expect(screen.getByRole('alert').textContent).toBe('Enter a whole number of hours from 2 to 100.');
    const days = screen.getByLabelText('Remember me length (days)') as HTMLInputElement;
    expect(days.max).toBe('60');
  });

  it('flags an out-of-range days entry', async () => {
    renderSection();
    const days = await screen.findByLabelText('Remember me length (days)');
    fireEvent.change(days, { target: { value: '366' } });
    expect(days.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByRole('alert').textContent).toBe('Enter a whole number of days from 1 to 365.');
    expect(saveButton().disabled).toBe(true);
  });

  it('shows the server message on a 400 and keeps the draft', async () => {
    mockUpdateSettings.mockRejectedValue(new ApiError('sessionLifetimeHours must be 1 to 8760', 400));
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)') as HTMLInputElement;
    fireEvent.change(hours, { target: { value: '48' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('sessionLifetimeHours must be 1 to 8760'));
    expect(hours.value).toBe('48');
  });

  it('shows Saving... while the request is pending', async () => {
    let release: (v: unknown) => void = () => {};
    mockUpdateSettings.mockReturnValue(new Promise((r) => { release = r; }));
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)');
    fireEvent.change(hours, { target: { value: '48' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveButton().textContent).toBe('Saving...'));
    expect(saveButton().disabled).toBe(true);
    release({ message: 'ok' });
    await waitFor(() => expect(saveButton().textContent).toBe('Save session lengths'));
  });

  it('registers an unsaved draft while the fields differ from the saved values', async () => {
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)');
    expect(hasUnsavedChanges()).toBe(false);
    fireEvent.change(hours, { target: { value: '48' } });
    await waitFor(() => expect(hasUnsavedChanges()).toBe(true));
    fireEvent.change(hours, { target: { value: '24' } });
    await waitFor(() => expect(hasUnsavedChanges()).toBe(false));
  });

  it('clears the guard and shows the saved values after a successful save', async () => {
    renderSection();
    const hours = await screen.findByLabelText('Session length (hours)') as HTMLInputElement;
    const days = screen.getByLabelText('Remember me length (days)') as HTMLInputElement;
    mockGetSettings.mockResolvedValue({
      jitBlockedUserAgents: { value: [], isDefault: true },
      sessionLifetimeHours: { value: 48, isDefault: false },
      sessionRememberDays: { value: 90, isDefault: false },
    });
    fireEvent.change(hours, { target: { value: '48' } });
    fireEvent.change(days, { target: { value: '90' } });
    await waitFor(() => expect(hasUnsavedChanges()).toBe(true));
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.queryByRole('status')?.textContent).toBe('Session lengths saved.'));
    expect(hasUnsavedChanges()).toBe(false);
    expect(hours.value).toBe('48');
    expect(days.value).toBe('90');
    expect(saveButton().disabled).toBe(true);
  });
});
