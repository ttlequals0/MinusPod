import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import {
  getEmailNotificationSettings, updateEmailNotificationSettings, sendTestEmail,
} from '../../api/settings';
import type { EmailNotificationSettings, EmailNotificationSettingsPayload } from '../../api/settings';
import { useTransientState } from '../../hooks/useTransientState';
import { EVENT_OPTIONS } from './notificationEvents';
import { btnPrimary, btnSecondary } from '../../components/buttonStyles';

interface EmailDraft {
  enabled: boolean;
  events: string[];
  smtpHost: string;
  smtpPort: string;
  smtpSecurity: 'none' | 'starttls' | 'ssl';
  smtpUsername: string;
  smtpPassword: string;
  fromAddress: string;
  recipients: string;
}

function draftFromSettings(s: EmailNotificationSettings): EmailDraft {
  return {
    enabled: s.enabled,
    events: [...s.events],
    smtpHost: s.smtpHost,
    smtpPort: String(s.smtpPort),
    smtpSecurity: s.smtpSecurity,
    smtpUsername: s.smtpUsername,
    smtpPassword: '',
    fromAddress: s.fromAddress,
    recipients: s.recipients,
  };
}

const emailInputClass =
  'w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground '
  + 'placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-sm';

function EmailSettingsForm() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<EmailDraft | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [testResult, setTestResult] = useTransientState<{ success: boolean; message: string } | null>(null, 4000);

  const { data: settings, isLoading, isError } = useQuery({
    queryKey: ['emailNotifications'],
    queryFn: getEmailNotificationSettings,
  });

  useSyncFromQuery(settings, (s) => setDraft(draftFromSettings(s)));

  const saveMutation = useMutation({
    mutationFn: (payload: EmailNotificationSettingsPayload) =>
      updateEmailNotificationSettings(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['emailNotifications'], data);
      setShowPassword(false);
    },
  });

  const testMutation = useMutation({
    mutationFn: sendTestEmail,
    onSuccess: (data) => setTestResult(data),
    onError: () => setTestResult({ success: false, message: 'Request failed' }),
  });

  if (isError) {
    return <p className="text-sm text-destructive">Failed to load email settings.</p>;
  }
  if (isLoading || !draft) {
    return <p className="text-sm text-muted-foreground">Loading email settings...</p>;
  }

  const dirty = settings != null && (
    draft.enabled !== settings.enabled
    || draft.smtpHost !== settings.smtpHost
    || draft.smtpPort !== String(settings.smtpPort)
    || draft.smtpSecurity !== settings.smtpSecurity
    || draft.smtpUsername !== settings.smtpUsername
    || draft.fromAddress !== settings.fromAddress
    || draft.recipients !== settings.recipients
    || draft.smtpPassword !== ''
    || draft.events.length !== settings.events.length
    || draft.events.some((e) => !settings.events.includes(e))
  );

  const savedSendReady = settings != null && settings.enabled
    && !!settings.smtpHost && !!settings.fromAddress && !!settings.recipients;

  function set<K extends keyof EmailDraft>(key: K, value: EmailDraft[K]) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function toggleEvent(event: string) {
    setDraft((prev) => {
      if (!prev) return prev;
      const events = prev.events.includes(event)
        ? prev.events.filter((e) => e !== event)
        : [...prev.events, event];
      return { ...prev, events };
    });
  }

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!draft) return;
    const port = parseInt(draft.smtpPort, 10);
    const payload: EmailNotificationSettingsPayload = {
      enabled: draft.enabled,
      events: draft.events,
      smtpHost: draft.smtpHost.trim(),
      smtpPort: Number.isNaN(port) ? undefined : port,
      smtpSecurity: draft.smtpSecurity,
      smtpUsername: draft.smtpUsername.trim(),
      fromAddress: draft.fromAddress.trim(),
      recipients: draft.recipients,
    };
    if (draft.smtpPassword) {
      payload.smtpPassword = draft.smtpPassword;
    }
    saveMutation.mutate(payload);
  }

  return (
    <form onSubmit={handleSave} className="space-y-4 p-4 rounded-lg border border-border bg-background">
      <p className="text-xs text-muted-foreground">
        Send an email through your own SMTP server when the selected events happen.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="sm:col-span-2">
          <label htmlFor="email-smtp-host" className="block text-sm font-medium text-foreground mb-1">
            SMTP host
          </label>
          <input
            id="email-smtp-host"
            type="text"
            value={draft.smtpHost}
            onChange={(e) => set('smtpHost', e.target.value)}
            placeholder="mail.example.com"
            className={emailInputClass}
          />
        </div>
        <div>
          <label htmlFor="email-smtp-port" className="block text-sm font-medium text-foreground mb-1">
            Port
          </label>
          <input
            id="email-smtp-port"
            type="number"
            min={1}
            max={65535}
            value={draft.smtpPort}
            onChange={(e) => set('smtpPort', e.target.value)}
            className={emailInputClass}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label htmlFor="email-smtp-security" className="block text-sm font-medium text-foreground mb-1">
            Security
          </label>
          <select
            id="email-smtp-security"
            value={draft.smtpSecurity}
            onChange={(e) => set('smtpSecurity', e.target.value as EmailDraft['smtpSecurity'])}
            className={emailInputClass}
          >
            <option value="none">None</option>
            <option value="starttls">STARTTLS</option>
            <option value="ssl">SSL/TLS</option>
          </select>
        </div>
        <div>
          <label htmlFor="email-smtp-username" className="block text-sm font-medium text-foreground mb-1">
            Username <span className="text-muted-foreground font-normal">(optional)</span>
          </label>
          <input
            id="email-smtp-username"
            type="text"
            value={draft.smtpUsername}
            onChange={(e) => set('smtpUsername', e.target.value)}
            autoComplete="off"
            className={emailInputClass}
          />
        </div>
        <div>
          <label htmlFor="email-smtp-password" className="block text-sm font-medium text-foreground mb-1">
            Password <span className="text-muted-foreground font-normal">(optional)</span>
          </label>
          <div className="relative">
            <input
              id="email-smtp-password"
              type={showPassword ? 'text' : 'password'}
              value={draft.smtpPassword}
              onChange={(e) => set('smtpPassword', e.target.value)}
              placeholder={settings?.smtpPasswordConfigured
                ? '(stored - enter new value to change)'
                : ''}
              autoComplete="off"
              className={`${emailInputClass} pr-16`}
            />
            <button
              type="button"
              onClick={() => setShowPassword((prev) => !prev)}
              className="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label htmlFor="email-from" className="block text-sm font-medium text-foreground mb-1">
            From address
          </label>
          <input
            id="email-from"
            type="email"
            value={draft.fromAddress}
            onChange={(e) => set('fromAddress', e.target.value)}
            placeholder="minuspod@example.com"
            className={emailInputClass}
          />
        </div>
        <div>
          <label htmlFor="email-recipients" className="block text-sm font-medium text-foreground mb-1">
            Recipients
          </label>
          <input
            id="email-recipients"
            type="text"
            value={draft.recipients}
            onChange={(e) => set('recipients', e.target.value)}
            placeholder="you@example.com, other@example.com"
            className={emailInputClass}
          />
          <p className="text-xs text-muted-foreground mt-1">Comma-separated email addresses</p>
        </div>
      </div>

      <div>
        <span className="block text-sm font-medium text-foreground mb-1">Events</span>
        <div className="space-y-1.5">
          {EVENT_OPTIONS.map((opt) => (
            <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={draft.events.includes(opt.value)}
                onChange={() => toggleEvent(opt.value)}
                className="rounded border-input"
              />
              <span className="text-sm text-foreground">{opt.label}</span>
            </label>
          ))}
        </div>
      </div>

      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => set('enabled', e.target.checked)}
          className="rounded border-input"
        />
        <span className="text-sm text-foreground">Enabled</span>
      </label>

      {saveMutation.isError && (
        <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
          {(saveMutation.error as Error)?.message || 'Failed to save email settings'}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="submit"
          disabled={saveMutation.isPending || !dirty}
          className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm`}
        >
          {saveMutation.isPending ? 'Saving...' : 'Save'}
        </button>
        <button
          type="button"
          onClick={() => testMutation.mutate()}
          disabled={testMutation.isPending || !savedSendReady || dirty}
          className={`px-4 py-2 rounded-lg ${btnSecondary} disabled:opacity-50 transition-colors text-sm`}
        >
          {testMutation.isPending ? 'Sending...' : 'Send test email'}
        </button>
        {dirty && (
          <span className="text-xs text-muted-foreground">
            The test uses saved settings; save your changes first.
          </span>
        )}
        {!dirty && !savedSendReady && (
          <span className="text-xs text-muted-foreground">
            Save an SMTP host, from address, and recipients, then turn email on to test.
          </span>
        )}
        {testResult && (
          <span className={`text-xs ${testResult.success ? 'text-green-500' : 'text-destructive'}`}>
            {testResult.message}
          </span>
        )}
      </div>
    </form>
  );
}

export default EmailSettingsForm;
