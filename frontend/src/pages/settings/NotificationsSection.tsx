import { useDraftClose } from '../../hooks/useDraftClose';
import { ConfirmModal } from '../../components/Modal';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import {
  getWebhooks, createWebhook, updateWebhook, deleteWebhook,
  testWebhook, validateTemplate,
} from '../../api/settings';
import type { Webhook, WebhookPayload, WebhookTestResult } from '../../api/settings';
import { useTransientState } from '../../hooks/useTransientState';
import EmailSettingsForm from './EmailSettingsForm';
import TimezoneSettingsForm from './TimezoneSettingsForm';
import { EVENT_OPTIONS } from './notificationEvents';
import Checkbox from '../../components/Checkbox';
import { btnBase, btnLg, btnPrimary, btnSecondary, btnGhost, btnDestructive } from '../../components/buttonStyles';
import { inputBase, textareaBase, labelBase } from '../../components/fieldStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { subHeading } from '../../components/pageStyles';

const DEFAULT_TEMPLATE_PLACEHOLDER = [
  'Leave blank to use default payload. Example custom template:',
  '{',
  '  "title": "{{ episode.title }}",',
  '  "message": "Removed {{ episode.ads_removed }} ads. Cost ${{ \'%.2f\' % episode.llm_cost }}.",',
  '  "url": "{{ episode.url }}"',
  '}',
].join('\n');

interface WebhookFormData {
  url: string;
  events: string[];
  enabled: boolean;
  secret: string;
  payloadTemplate: string;
  contentType: string;
}

const emptyForm: WebhookFormData = {
  url: '',
  events: [],
  enabled: true,
  secret: '',
  payloadTemplate: '',
  contentType: 'application/json',
};

function WebhooksBlock() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<WebhookFormData>({ ...emptyForm });
  const [showSecret, setShowSecret] = useState(false);
  const [baseline, setBaseline] = useState<WebhookFormData>(emptyForm);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  // One transient result at a time; auto-clears 4s after the last test.
  // `results` carries a per-event breakdown; only the summary line is shown below.
  const [testResult, setTestResult] = useTransientState<
    ({ id: string } & WebhookTestResult) | null
  >(null, 4000);
  const [templatePreview, setTemplatePreview] = useState<{ valid: boolean; preview: string; error: string | null } | null>(null);
  const [validating, setValidating] = useState(false);

  const { data: webhooks = [], isLoading } = useQuery({
    queryKey: ['webhooks'],
    queryFn: getWebhooks,
  });

  const createMutation = useMutation({
    mutationFn: (payload: WebhookPayload) => createWebhook(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      resetForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<WebhookPayload> }) => updateWebhook(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      resetForm();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteWebhook(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => testWebhook(id),
    onSuccess: (data, id) => setTestResult({ id, ...data }),
    onError: (_err, id) => setTestResult({ id, success: false, results: [], message: 'Could not send test webhook.' }),
  });

  function resetForm() {
    setForm({ ...emptyForm });
    setBaseline(emptyForm);
    setShowForm(false);
    setEditingId(null);
    setShowSecret(false);
    setTemplatePreview(null);
  }

  function startEdit(webhook: Webhook) {
    const next = {
      url: webhook.url,
      events: [...webhook.events],
      enabled: webhook.enabled,
      secret: '',
      payloadTemplate: webhook.payloadTemplate || '',
      contentType: webhook.contentType,
    };
    setForm(next);
    setBaseline(next);
    setEditingId(webhook.id);
    setShowForm(true);
    setTemplatePreview(null);
  }

  function handleEventToggle(event: string) {
    setForm((prev) => ({
      ...prev,
      events: prev.events.includes(event)
        ? prev.events.filter((e) => e !== event)
        : [...prev.events, event],
    }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const payload: WebhookPayload = {
      url: form.url,
      events: form.events,
      enabled: form.enabled,
      payloadTemplate: form.payloadTemplate || null,
      contentType: form.contentType,
    };
    if (form.secret) {
      payload.secret = form.secret;
    }

    if (editingId) {
      updateMutation.mutate({ id: editingId, payload });
    } else {
      createMutation.mutate(payload);
    }
  }

  async function handleValidateTemplate() {
    if (!form.payloadTemplate.trim()) return;
    setValidating(true);
    try {
      const result = await validateTemplate(form.payloadTemplate);
      setTemplatePreview(result);
    } catch {
      setTemplatePreview({ valid: false, preview: '', error: 'Could not validate template.' });
    } finally {
      setValidating(false);
    }
  }

  const isSaving = createMutation.isPending || updateMutation.isPending;
  const { requestClose, confirmation } = useDraftClose(
    showForm && JSON.stringify(form) !== JSON.stringify(baseline), resetForm, isSaving,
  );

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">Send notifications to HTTP endpoints.</p>
      {isLoading && (
        <p className="text-sm text-muted-foreground">Loading webhooks...</p>
      )}

      {!isLoading && webhooks.length === 0 && !showForm && (
        <p className="text-sm text-muted-foreground">No webhooks yet.</p>
      )}

      {/* Webhook list */}
      {webhooks.length > 0 && (
        <div className="space-y-2">
          {webhooks.map((wh) => (
            <div
              key={wh.id}
              className="flex flex-col items-stretch justify-between gap-3 p-3 rounded-lg bg-muted/40 sm:flex-row sm:items-center"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="w-full min-w-0 break-all text-sm font-mono" title={wh.url}>
                    {wh.url}
                  </span>
                  {wh.payloadTemplate && (
                    <span className={`${badgeBase} ${tint.blue}`}>
                      custom template
                    </span>
                  )}
                  <span
                    className={`${badgeBase} ${
                      wh.enabled
                        ? tint.success
                        : tint.neutral
                    }`}
                  >
                    {wh.enabled ? 'enabled' : 'disabled'}
                  </span>
                </div>
                <div className="flex gap-1.5 mt-1 flex-wrap">
                  {wh.events.map((ev) => {
                    const label = EVENT_OPTIONS.find((o) => o.value === ev)?.label || ev;
                    return (
                      <span
                        key={ev}
                        className={`${badgeBase} ${tint.secondary} whitespace-nowrap shrink-0`}
                      >
                        {label}
                      </span>
                    );
                  })}
                </div>
                {testResult?.id === wh.id && (
                  <p
                    className={`text-xs mt-1 ${
                      !testResult.success
                        ? 'text-destructive'
                        : testResult.results.some((r) => r.templateFallback)
                          ? 'text-warning'
                          : 'text-success'
                    }`}
                  >
                    {testResult.message}
                  </p>
                )}
              </div>

              <div className="flex items-center gap-1.5 shrink-0 self-end sm:self-auto">
                <button
                  onClick={() => testMutation.mutate(wh.id)}
                  disabled={testMutation.isPending}
                  className={`${btnBase} ${btnSecondary}`}
                >
                  Test
                </button>
                <button
                  onClick={() => startEdit(wh)}
                  disabled={showForm}
                  className={`${btnBase} ${btnSecondary}`}
                >
                  Edit
                </button>
                <button
                  onClick={() => setDeleteConfirmId(wh.id)}
                  disabled={deleteMutation.isPending}
                  className={`${btnBase} ${btnDestructive}`}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add button */}
      {!showForm && (
        <button
          onClick={() => {
            setForm({ ...emptyForm });
            setBaseline(emptyForm);
            setEditingId(null);
            setShowForm(true);
            setTemplatePreview(null);
          }}
          className={`${btnLg} ${btnPrimary}`}
        >
          Add webhook
        </button>
      )}

      {/* Form */}
      {showForm && (
        <form aria-describedby={createMutation.isError || updateMutation.isError ? "webhook-save-error" : undefined} onSubmit={handleSubmit} className="space-y-4 p-4 rounded-lg bg-muted/40">
      <fieldset disabled={isSaving} className="space-y-4">
          <h3 className={subHeading}>
            {editingId ? 'Edit webhook' : 'New webhook'}
          </h3>

          {/* URL */}
          <div>
            <label htmlFor="webhook-url" className={labelBase}>
              URL
            </label>
            <input
              id="webhook-url"
              type="url"
              required
              value={form.url}
              onChange={(e) => setForm((prev) => ({ ...prev, url: e.target.value }))}
              placeholder="https://example.com/webhook"
              className={`${inputBase} w-full`}
            />
          </div>

          {/* Events */}
          <div>
            <span className="block text-sm font-medium text-foreground mb-1">Events</span>
            <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {EVENT_OPTIONS.map((opt) => (
                <Checkbox
                  key={opt.value}
                  checked={form.events.includes(opt.value)}
                  onChange={() => handleEventToggle(opt.value)}
                  label={opt.label}
                  className="max-sm:min-h-11 max-sm:min-w-11"
                />
              ))}
            </div>
          </div>

          {/* Payload Template */}
          <div>
            <label htmlFor="webhook-template" className={labelBase}>
              Payload template (optional)
            </label>
            <p id="webhook-template-help" className="text-xs text-muted-foreground mb-1">
              Customize the JSON body sent to your endpoint
            </p>
            <textarea
              id="webhook-template"
              aria-invalid={templatePreview?.valid === false}
              aria-describedby={`webhook-template-help${templatePreview ? " webhook-template-result" : ""}`}
              value={form.payloadTemplate}
              onChange={(e) => {
                setForm((prev) => ({ ...prev, payloadTemplate: e.target.value }));
                setTemplatePreview(null);
              }}
              placeholder={DEFAULT_TEMPLATE_PLACEHOLDER}
              rows={6}
              className={`${textareaBase} w-full font-mono`}
            />
            <div className="flex items-center gap-2 mt-1">
              <button
                type="button"
                onClick={handleValidateTemplate}
                disabled={validating || !form.payloadTemplate.trim()}
                className={`${btnBase} ${btnSecondary}`}
              >
                {validating ? 'Validating...' : 'Validate and preview'}
              </button>
            </div>
            {templatePreview && (
              <div
                id="webhook-template-result" role="status"
                className={`mt-2 p-3 rounded-lg text-xs font-mono whitespace-pre-wrap ${
                  templatePreview.valid
                    ? 'bg-success/10 text-success'
                    : 'bg-destructive/10 text-destructive'
                }`}
              >
                {templatePreview.valid ? templatePreview.preview : templatePreview.error}
              </div>
            )}
          </div>

          {/* Content Type */}
          <div>
            <label htmlFor="webhook-content-type" className={labelBase}>
              Content-Type
            </label>
            <input
              id="webhook-content-type"
              type="text"
              value={form.contentType}
              onChange={(e) => setForm((prev) => ({ ...prev, contentType: e.target.value }))}
              className={`${inputBase} w-full`}
            />
          </div>

          {/* Secret */}
          <div>
            <label htmlFor="webhook-secret" className={labelBase}>
              Secret {editingId && <span className="text-muted-foreground font-normal">(leave empty to keep current)</span>}
            </label>
            <div className="relative">
              <input
                id="webhook-secret"
                type={showSecret ? 'text' : 'password'}
                value={form.secret}
                onChange={(e) => setForm((prev) => ({ ...prev, secret: e.target.value }))}
                placeholder="Optional signing secret"
                autoComplete="off"
                className={`${inputBase} w-full pr-16`}
              />
              <button
                type="button"
                onClick={() => setShowSecret((prev) => !prev)}
                className={`${btnBase} ${btnGhost} absolute right-1 top-1/2 -translate-y-1/2`}
              >
                {showSecret ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {/* Enabled */}
          <Checkbox
            checked={form.enabled}
            onChange={(v) => setForm((prev) => ({ ...prev, enabled: v }))}
            label="Enabled"
            className="flex max-sm:min-h-11 max-sm:min-w-11"
          />

          {/* Error messages */}
          {(createMutation.isError || updateMutation.isError) && (
            <div id="webhook-save-error" role="alert" className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
              {((createMutation.error || updateMutation.error) as Error)?.message || 'Could not save webhook.'}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={isSaving || form.events.length === 0}
              className={`${btnLg} ${btnPrimary}`}
            >
              {isSaving ? 'Saving...' : editingId ? 'Update webhook' : 'Create webhook'}
            </button>
            <button
              type="button"
              onClick={requestClose}
              className={`${btnLg} ${btnSecondary}`}
            >
              Cancel
            </button>
          </div>
              </fieldset>
    </form>
      )}
      {confirmation}
      {deleteConfirmId && <ConfirmModal title="Delete webhook?" confirmLabel="Delete webhook"
        pending={deleteMutation.isPending} onCancel={() => setDeleteConfirmId(null)}
        onConfirm={() => deleteMutation.mutate(deleteConfirmId, { onSuccess: () => setDeleteConfirmId(null) })}>
        <p>This removes the selected notification endpoint.</p>
        {deleteMutation.isError && <p role="alert" className="text-destructive">Could not delete webhook.</p>}
      </ConfirmModal>}
    </div>
  );
}

function NotificationsSection() {
  return (
    <CollapsibleSection title="Notifications" storageKey="settings-section-notifications" defaultOpen>
      <div className="space-y-4">
        <TimezoneSettingsForm />
        <CollapsibleSection
          title="Email"
          storageKey="settings-section-notifications-email"
          defaultOpen
          variant="subsection"
        >
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Send email notifications through your own SMTP server when the selected events happen.
            </p>
            <EmailSettingsForm />
          </div>
        </CollapsibleSection>
        <CollapsibleSection
          title="Webhooks"
          storageKey="settings-section-notifications-webhooks"
          defaultOpen
          variant="subsection"
        >
          <WebhooksBlock />
        </CollapsibleSection>
      </div>
    </CollapsibleSection>
  );
}

export default NotificationsSection;
