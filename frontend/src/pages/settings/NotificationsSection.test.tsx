/**
 * Tests for the Notifications settings section: email settings form
 * (render, save payload shape, test button) and the webhook list still
 * rendering under its sub-heading.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import NotificationsSection from './NotificationsSection';
import type { EmailNotificationSettings } from '../../api/settings';
import SearchableSectionGroup from '../../components/SearchableSectionGroup';
import { SettingsBulkCollapseProvider, type SettingsBulkCollapseSignal } from '../../context/SettingsBulkCollapseContext';

const EMAIL_SECTION_KEY = 'settings-section-notifications-email';
const WEBHOOKS_SECTION_KEY = 'settings-section-notifications-webhooks';

const mockGetEmail = vi.fn();
const mockUpdateEmail = vi.fn();
const mockSendTest = vi.fn();
const mockGetWebhooks = vi.fn();
const mockCreateWebhook = vi.fn();
const mockTestWebhook = vi.fn();
const mockGetTimezone = vi.fn();
const mockUpdateTimezone = vi.fn();

vi.mock('../../api/settings', () => ({
  getWebhooks: (...a: unknown[]) => mockGetWebhooks(...a),
  createWebhook: (...a: unknown[]) => mockCreateWebhook(...a),
  updateWebhook: vi.fn(),
  deleteWebhook: vi.fn(),
  testWebhook: (...a: unknown[]) => mockTestWebhook(...a),
  validateTemplate: vi.fn(),
  getEmailNotificationSettings: (...a: unknown[]) => mockGetEmail(...a),
  updateEmailNotificationSettings: (...a: unknown[]) => mockUpdateEmail(...a),
  sendTestEmail: (...a: unknown[]) => mockSendTest(...a),
  getNotificationTimezone: (...a: unknown[]) => mockGetTimezone(...a),
  updateNotificationTimezone: (...a: unknown[]) => mockUpdateTimezone(...a),
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

function sectionTree(
  bulkSignal: SettingsBulkCollapseSignal | null = null,
) {
  return (
    <SettingsBulkCollapseProvider value={bulkSignal}>
      <QueryClientProvider client={makeClient()}>
        <NotificationsSection />
      </QueryClientProvider>
    </SettingsBulkCollapseProvider>
  );
}

function searchableSectionTree() {
  return (
    <QueryClientProvider client={makeClient()}>
      <SearchableSectionGroup
        placeholder="Search notifications"
        ariaLabel="Search notifications"
        clearLabel="Clear notification search"
      >
        <NotificationsSection />
      </SearchableSectionGroup>
    </QueryClientProvider>
  );
}

function renderSection() {
  return render(sectionTree());
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  mockGetEmail.mockResolvedValue(makeSettings());
  mockGetWebhooks.mockResolvedValue([
    { id: 'wh1', url: 'http://hook.example.com/x', events: ['Episode Failed'],
      enabled: true, payloadTemplate: null, contentType: 'application/json' },
  ]);
  mockGetTimezone.mockResolvedValue({ timezone: 'UTC' });
});

describe('NotificationsSection', () => {
  it('renders email fields from query data and the webhook list', async () => {
    renderSection();
    await waitFor(() => {
      expect((screen.getByLabelText('SMTP host') as HTMLInputElement).value).toBe('mail.example.com');
    });
    expect((screen.getByLabelText('From address') as HTMLInputElement).value).toBe('minuspod@example.com');
    expect((screen.getByLabelText('Recipients') as HTMLInputElement).value).toBe('op@example.com');
    expect(screen.getByRole('heading', { name: 'Notifications: Email' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Notifications: Webhooks' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Timezone' })).toBeDefined();
    expect(screen.getByText('http://hook.example.com/x')).toBeDefined();
  });

  it('persists email and webhook collapse state independently', async () => {
    const user = userEvent.setup();
    const view = renderSection();
    const emailToggle = screen.getByRole('button', { name: 'Notifications: Email' });
    const webhooksToggle = screen.getByRole('button', { name: 'Notifications: Webhooks' });

    await user.click(emailToggle);

    expect(emailToggle.getAttribute('aria-expanded')).toBe('false');
    expect(webhooksToggle.getAttribute('aria-expanded')).toBe('true');
    expect(JSON.parse(localStorage.getItem(EMAIL_SECTION_KEY)!)).toBe(false);
    expect(JSON.parse(localStorage.getItem(WEBHOOKS_SECTION_KEY)!)).toBe(true);

    view.unmount();
    renderSection();
    expect(screen.getByRole('button', { name: 'Notifications: Email' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByRole('button', { name: 'Notifications: Webhooks' }).getAttribute('aria-expanded')).toBe('true');
  });

  it('keeps an email draft mounted while its section is collapsed', async () => {
    const user = userEvent.setup();
    renderSection();
    const smtpHost = await screen.findByLabelText('SMTP host');
    await user.type(smtpHost, 'draft');
    await user.click(screen.getByRole('button', { name: 'Notifications: Email' }));
    await user.click(screen.getByRole('button', { name: 'Notifications: Email' }));

    expect((screen.getByLabelText('SMTP host') as HTMLInputElement).value).toBe('mail.example.comdraft');
  });

  it.each([
    ['notifications', ['Notifications: Email', 'Notifications: Webhooks']],
    ['webhooks', ['Notifications: Webhooks']],
  ])('searching "%s" reveals matching collapsed sections and restores saved states', async (query, titles) => {
    localStorage.setItem(EMAIL_SECTION_KEY, 'false');
    localStorage.setItem(WEBHOOKS_SECTION_KEY, 'false');
    const user = userEvent.setup();
    render(searchableSectionTree());
    const search = screen.getByRole('textbox', { name: 'Search notifications' });

    await user.type(search, query);
    for (const title of titles) {
      expect(screen.getByRole('button', { name: title }).getAttribute('aria-expanded')).toBe('true');
    }
    if (query === 'webhooks') {
      expect(screen.getByRole('button', { name: 'Notifications: Email' }).getAttribute('aria-expanded')).toBe('false');
    }

    await user.click(screen.getByRole('button', { name: 'Clear notification search' }));
    expect(screen.getByRole('button', { name: 'Notifications: Email' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByRole('button', { name: 'Notifications: Webhooks' }).getAttribute('aria-expanded')).toBe('false');
  });

  it('responds to settings expand and collapse all signals', () => {
    const view = renderSection();
    view.rerender(sectionTree({ seq: 1, open: false }));
    for (const title of ['Timezone', 'Notifications: Email', 'Notifications: Webhooks']) {
      expect(screen.getByRole('button', { name: title }).getAttribute('aria-expanded')).toBe('false');
    }

    view.rerender(sectionTree({ seq: 2, open: true }));
    for (const title of ['Timezone', 'Notifications: Email', 'Notifications: Webhooks']) {
      expect(screen.getByRole('button', { name: title }).getAttribute('aria-expanded')).toBe('true');
    }
  });

  it('shows the full webhook URL and event label', async () => {
    const longUrl = `https://hooks.example.com/${'pathsegment'.repeat(12)}`;
    mockGetWebhooks.mockResolvedValue([
      { id: 'wh-long', url: longUrl, events: ['Service Offline'], enabled: true,
        payloadTemplate: null, contentType: 'application/json' },
    ]);
    const view = renderSection();

    const webhooksSection = view.container.querySelector<HTMLElement>(`[data-search-key="${WEBHOOKS_SECTION_KEY}"]`);
    expect(webhooksSection).not.toBeNull();
    expect(await within(webhooksSection!).findByText(longUrl)).toBeDefined();
    expect(within(webhooksSection!).getByText('Service Offline')).toBeDefined();
  });

  it('loads and saves the notification timezone', async () => {
    mockUpdateTimezone.mockResolvedValue({ timezone: 'America/New_York' });
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect((screen.getByLabelText('Timezone') as HTMLSelectElement).value).toBe('UTC');
    });
    await user.selectOptions(screen.getByLabelText('Timezone'), 'America/New_York');
    await user.click(screen.getByRole('button', { name: 'Save timezone' }));
    await waitFor(() => expect(mockUpdateTimezone).toHaveBeenCalledWith('America/New_York'));
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

  it('shows the per-event summary message after a webhook test', async () => {
    mockTestWebhook.mockResolvedValue({
      success: true,
      results: [
        { event: 'Episode Failed', delivered: true },
        { event: 'Auth Failure', delivered: true },
      ],
      message: '2 of 2 test payloads delivered',
    });
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText('http://hook.example.com/x')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Test' }));
    await waitFor(() => {
      expect(screen.getByText('2 of 2 test payloads delivered')).toBeDefined();
    });
    expect(mockTestWebhook).toHaveBeenCalledWith('wh1');
  });

  it('shows an uncertain result when webhook creation loses its response', async () => {
    mockGetWebhooks.mockResolvedValue([]);
    mockCreateWebhook.mockRejectedValue(new Error(
      'The server did not confirm this change. It may have completed. Check the current state before trying again.',
    ));
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('No webhooks configured.')).toBeDefined());
    await user.click(screen.getByRole('button', { name: 'Add Webhook' }));
    await user.type(screen.getByLabelText('URL'), 'https://example.com/hook');
    const eventChoices = screen.getAllByLabelText('Episode Failed');
    await user.click(eventChoices[eventChoices.length - 1]);
    await user.click(screen.getByRole('button', { name: 'Create Webhook' }));

    expect(await screen.findByText(/may have completed/)).toBeDefined();
    expect(mockCreateWebhook).toHaveBeenCalledTimes(1);
  });

  it('shows a partial-failure summary message when some events fail to deliver', async () => {
    mockTestWebhook.mockResolvedValue({
      success: false,
      results: [
        { event: 'Episode Failed', delivered: true },
        { event: 'Auth Failure', delivered: false },
      ],
      message: '1 of 2 test payloads delivered',
    });
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText('http://hook.example.com/x')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Test' }));
    const message = await screen.findByText('1 of 2 test payloads delivered');
    expect(message.className).toContain('text-destructive');
  });

  it('shows the summary as a warning when a template fell back for some events', async () => {
    mockTestWebhook.mockResolvedValue({
      success: true,
      results: [
        { event: 'Episode Failed', delivered: true, templateFallback: false },
        { event: 'Auth Failure', delivered: true, templateFallback: true },
      ],
      message: '2 of 2 test payloads delivered; template could not render for 1 event (default payload used): Auth Failure',
    });
    renderSection();
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText('http://hook.example.com/x')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Test' }));
    const message = await screen.findByText(/template could not render for 1 event/);
    expect(message.className).toContain('text-warning');
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
