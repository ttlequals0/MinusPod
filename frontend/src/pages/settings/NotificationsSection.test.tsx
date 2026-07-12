/**
 * Tests for the Notifications settings section: email settings form
 * (render, save payload shape, test button) and the webhook list still
 * rendering under its sub-heading.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import NotificationsSection from './NotificationsSection';
import type { EmailNotificationSettings } from '../../api/settings';

const mockGetEmail = vi.fn();
const mockUpdateEmail = vi.fn();
const mockSendTest = vi.fn();
const mockGetWebhooks = vi.fn();

vi.mock('../../api/settings', () => ({
  getWebhooks: (...a: unknown[]) => mockGetWebhooks(...a),
  createWebhook: vi.fn(),
  updateWebhook: vi.fn(),
  deleteWebhook: vi.fn(),
  testWebhook: vi.fn(),
  validateTemplate: vi.fn(),
  getEmailNotificationSettings: (...a: unknown[]) => mockGetEmail(...a),
  updateEmailNotificationSettings: (...a: unknown[]) => mockUpdateEmail(...a),
  sendTestEmail: (...a: unknown[]) => mockSendTest(...a),
}));

function makeSettings(overrides: Partial<EmailNotificationSettings> = {}): EmailNotificationSettings {
  return {
    enabled: true,
    events: ['Episode Failed'],
    smtpHost: 'mail.example.com',
    smtpPort: 587,
    smtpSecurity: 'starttls',
    smtpUsername: '',
    smtpPasswordConfigured: false,
    fromAddress: 'minuspod@example.com',
    recipients: 'op@example.com',
    ...overrides,
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderSection() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <NotificationsSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetEmail.mockResolvedValue(makeSettings());
  mockGetWebhooks.mockResolvedValue([
    { id: 'wh1', url: 'http://hook.example.com/x', events: ['Episode Failed'],
      enabled: true, payloadTemplate: null, contentType: 'application/json' },
  ]);
});

describe('NotificationsSection', () => {
  it('renders email fields from query data and the webhook list', async () => {
    renderSection();
    await waitFor(() => {
      expect((screen.getByLabelText('SMTP host') as HTMLInputElement).value).toBe('mail.example.com');
    });
    expect((screen.getByLabelText('From address') as HTMLInputElement).value).toBe('minuspod@example.com');
    expect((screen.getByLabelText('Recipients') as HTMLInputElement).value).toBe('op@example.com');
    expect(screen.getByRole('heading', { name: 'Email' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Webhooks' })).toBeDefined();
    expect(screen.getByText('http://hook.example.com/x')).toBeDefined();
  });

  it('saves the draft without smtpPassword when the field is empty', async () => {
    mockUpdateEmail.mockResolvedValue(makeSettings({ smtpHost: 'new.example.com' }));
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect((screen.getByLabelText('SMTP host') as HTMLInputElement).value).toBe('mail.example.com');
    });
    const host = screen.getByLabelText('SMTP host');
    await user.clear(host);
    await user.type(host, 'new.example.com');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdateEmail).toHaveBeenCalledOnce());
    const payload = mockUpdateEmail.mock.calls[0][0];
    expect(payload.smtpHost).toBe('new.example.com');
    expect(payload).not.toHaveProperty('smtpPassword');
  });

  it('includes smtpPassword when typed', async () => {
    mockUpdateEmail.mockResolvedValue(makeSettings({ smtpPasswordConfigured: true }));
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByLabelText(/Password/)).toBeDefined();
    });
    await user.type(screen.getByLabelText(/Password/), 'hunter2');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdateEmail).toHaveBeenCalledOnce());
    expect(mockUpdateEmail.mock.calls[0][0].smtpPassword).toBe('hunter2');
  });

  it('sends a test email and shows the inline result', async () => {
    mockSendTest.mockResolvedValue({ success: true, message: 'Test email sent to 1 recipient(s)' });
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect((screen.getByRole('button', { name: 'Send test email' }) as HTMLButtonElement).disabled).toBe(false);
    });
    await user.click(screen.getByRole('button', { name: 'Send test email' }));
    await waitFor(() => {
      expect(screen.getByText('Test email sent to 1 recipient(s)')).toBeDefined();
    });
  });

  it('disables the test button when saved settings are not send-ready', async () => {
    mockGetEmail.mockResolvedValue(makeSettings({ enabled: false }));
    renderSection();
    await waitFor(() => {
      expect((screen.getByRole('button', { name: 'Send test email' }) as HTMLButtonElement).disabled).toBe(true);
    });
    expect(mockSendTest).not.toHaveBeenCalled();
  });

  it('disables the test button while the draft is dirty', async () => {
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect((screen.getByLabelText('SMTP host') as HTMLInputElement).value).toBe('mail.example.com');
    });
    await user.type(screen.getByLabelText('SMTP host'), 'x');
    expect((screen.getByRole('button', { name: 'Send test email' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/uses saved settings/)).toBeDefined();
  });
});
