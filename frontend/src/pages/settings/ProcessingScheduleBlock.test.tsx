import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ProcessingScheduleBlock from './ProcessingScheduleBlock';
import * as settingsApi from '../../api/settings';

vi.mock('../../api/settings', () => ({
  getProcessingSchedule: vi.fn(),
  updateProcessingSchedule: vi.fn(),
}));

const mocked = vi.mocked(settingsApi);

function schedule(overrides: Partial<settingsApi.ProcessingScheduleSettings> = {}) {
  return {
    enabled: true,
    start: '22:00',
    end: '06:00',
    days: ['mon', 'tue', 'wed', 'thu', 'fri'],
    timezone: 'America/New_York',
    open: true,
    nextChangeAt: '2026-11-10T11:00:00Z',
    ...overrides,
  } as settingsApi.ProcessingScheduleSettings;
}

function renderBlock() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProcessingScheduleBlock active />
    </QueryClientProvider>,
  );
}

const day = (name: string) => screen.getByRole('button', { name });

describe('ProcessingScheduleBlock', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getProcessingSchedule.mockResolvedValue(schedule());
    mocked.updateProcessingSchedule.mockResolvedValue(schedule());
  });

  it('renders the saved schedule', async () => {
    renderBlock();
    expect((await screen.findByLabelText('From') as HTMLInputElement).value).toBe('22:00');
    expect((screen.getByLabelText('Until') as HTMLInputElement).value).toBe('06:00');
    expect(screen.getByRole('switch', { name: 'Processing schedule toggle' }).getAttribute('aria-checked')).toBe('true');
    expect(day('Monday').getAttribute('aria-pressed')).toBe('true');
    expect(day('Saturday').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByLabelText('From').getAttribute('aria-describedby')).toBe('schedule-help');
    expect(document.getElementById('schedule-help')?.textContent).toContain('America/New_York');
  });

  it('shows a skeleton before the schedule loads', () => {
    mocked.getProcessingSchedule.mockReturnValue(new Promise(() => {}));
    renderBlock();
    expect(screen.queryByLabelText('From')).toBeNull();
  });

  it('reports the saved open window and when it closes', async () => {
    renderBlock();
    expect((await screen.findByText(/^Open now/)).textContent).toMatch(/^Open now\. Closes .+\.$/);
  });

  it('reports the saved closed window and when it opens', async () => {
    mocked.getProcessingSchedule.mockResolvedValue(schedule({ open: false }));
    renderBlock();
    expect((await screen.findByText(/^Closed now/)).textContent).toMatch(/^Closed now\. Opens .+\.$/);
  });

  it('shows no status line while the schedule is off', async () => {
    mocked.getProcessingSchedule.mockResolvedValue(schedule({ enabled: false, nextChangeAt: null }));
    renderBlock();
    await screen.findByLabelText('From');
    expect(screen.queryByText(/^(Open|Closed) now/)).toBeNull();
  });

  it('keeps the status line on the saved state while editing', async () => {
    renderBlock();
    const user = userEvent.setup();
    await screen.findByText(/^Open now/);
    await user.click(screen.getByRole('switch', { name: 'Processing schedule toggle' }));
    expect(screen.getByText(/^Open now/)).toBeDefined();
  });

  it('sends the edited fields on save and shows the saved badge', async () => {
    renderBlock();
    const user = userEvent.setup();
    await screen.findByLabelText('From');
    await user.click(day('Saturday'));
    await user.click(day('Monday'));
    await user.click(screen.getByRole('switch', { name: 'Processing schedule toggle' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateProcessingSchedule).toHaveBeenCalledWith({
      enabled: false, start: '22:00', end: '06:00', days: ['tue', 'wed', 'thu', 'fri', 'sat'],
    }));
    expect(await screen.findByText('Saved')).toBeDefined();
  });

  it('blocks save and explains when no day is selected', async () => {
    mocked.getProcessingSchedule.mockResolvedValue(schedule({ days: ['sun'] }));
    renderBlock();
    const user = userEvent.setup();
    await screen.findByLabelText('From');
    expect(screen.queryByText('Select at least one day.')).toBeNull();
    await user.click(day('Sunday'));
    expect(screen.getByRole('alert').textContent).toBe('Select at least one day.');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(day('Sunday'));
    expect(screen.queryByText('Select at least one day.')).toBeNull();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('blocks save when a time is cleared', async () => {
    renderBlock();
    const user = userEvent.setup();
    await user.clear(await screen.findByLabelText('From'));
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps the draft and shows the server message when save fails', async () => {
    mocked.updateProcessingSchedule.mockRejectedValue(new Error('Invalid time'));
    renderBlock();
    const user = userEvent.setup();
    await screen.findByLabelText('From');
    await user.click(day('Saturday'));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Invalid time')).toBeDefined();
    expect(day('Saturday').getAttribute('aria-pressed')).toBe('true');
  });

  it('does not render the form when the load fails, and retries', async () => {
    mocked.getProcessingSchedule.mockRejectedValueOnce(new Error('down'));
    renderBlock();
    const user = userEvent.setup();
    expect(await screen.findByText('Could not load the processing schedule.')).toBeDefined();
    expect(screen.queryByLabelText('From')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByLabelText('From')).toBeDefined();
  });
});
