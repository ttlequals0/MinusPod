import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import {
  getWebhooks, createWebhook, updateWebhook, deleteWebhook,
  testWebhook, validateTemplate,
} from '../../api/settings';
import type { Webhook, WebhookPayload } from '../../api/settings';

const EVENT_OPTIONS: { value: string; label: string }[] = [
  { value: 'Episode Processed', label: 'Episode Processed' },
  { value: 'Episode Failed', label: 'Episode Failed' },
];

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

function WebhooksSection() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<WebhookFormData>({ ...emptyForm });
  const [showSecret, setShowSecret] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({});
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
    onSuccess: (data, id) => {
      setTestResults((prev) => ({ ...prev, [id]: data }));
      setTimeout(() => {
        setTestResults((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }, 4000);
    },
    onError: (_err, id) => {
      setTestResults((prev) => ({ ...prev, [id]: { success: false, message: 'Request failed' } }));
      setTimeout(() => {
        setTestResults((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }, 4000);
    },
  });

  function resetForm() {
    setForm({ ...emptyForm });
    setShowForm(false);
    setEditingId(null);
    setShowSecret(false);
    setTemplatePreview(null);
  }

  function startEdit(webhook: Webhook) {
    setForm({
      url: webhook.url,
      events: [...webhook.events],
      enabled: webhook.enabled,
      secret: '',
      payloadTemplate: webhook.payloadTemplate || '',
      contentType: webhook.contentType,
    });
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

  function handleDeleteClick(id: string) {
    if (deleteConfirmId === id) {
      deleteMutation.mutate(id);
      setDeleteConfirmId(null);
    } else {
      setDeleteConfirmId(id);
      setTimeout(() => setDeleteConfirmId((current) => (current === id ? null : current)), 3000);
    }
  }

  async function handleValidateTemplate() {
    if (!form.payloadTemplate.trim()) return;
    setValidating(true);
    try {
      const result = await validateTemplate(form.payloadTemplate);
      setTemplatePreview(result);
    } catch {
      setTemplatePreview({ valid: false, preview: '', error: 'Validation request failed' });
    } finally {
      setValidating(false);
    }
  }

  const isSaving = createMutation.isPending || updateMutation.isPending;

  return (
    <CollapsibleSection title="Webhooks" storageKey="settings-section-webhooks">
      <div className="space-y-4">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading webhooks...</p>
        )}

        {!isLoading && webhooks.length === 0 && !showForm && (
          <p className="text-sm text-muted-foreground">No webhooks configured.</p>
        )}

        {/* Webhook list */}
        {webhooks.length > 0 && (
          <div className="space-y-2">
            {webhooks.map((wh) => (
              <div
                key={wh.id}
                className="flex items-center justify-between gap-3 p-3 rounded-lg border border-border bg-background"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-mono truncate max-w-xs" title={wh.url}>
                      {wh.url.length > 50 ? wh.url.slice(0, 50) + '...' : wh.url}
                    </span>
                    {wh.payloadTemplate && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-600 dark:text-blue-400">
                        custom template
                      </span>
                    )}
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded ${
                        wh.enabled
                          ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                          : 'bg-muted text-muted-foreground'
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
                          className="text-xs px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground"
                        >
                          {label}
                        </span>
                      );
                    })}
                  </div>
                  {testResults[wh.id] && (
                    <p
                      className={`text-xs mt-1 ${
                        testResults[wh.id].success ? 'text-green-500' : 'text-destructive'
                      }`}
                    >
                      {testResults[wh.id].message}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <button
                    onClick={() => testMutation.mutate(wh.id)}
                    disabled={testMutation.isPending}
                    className="px-2.5 py-1 text-xs rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
                  >
                    Test
                  </button>
                  <button
                    onClick={() => startEdit(wh)}
                    className="px-2.5 py-1 text-xs rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDeleteClick(wh.id)}
                    disabled={deleteMutation.isPending}
                    className="px-2.5 py-1 text-xs rounded bg-destructive/10 text-destructive hover:bg-destructive/20 disabled:opacity-50 transition-colors"
                  >
                    {deleteConfirmId === wh.id ? 'Confirm?' : 'Delete'}
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
              setEditingId(null);
              setShowForm(true);
              setTemplatePreview(null);
            }}
            className="px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors text-sm"
          >
            Add Webhook
          </button>
        )}

        {/* Form */}
        {showForm && (
          <form onSubmit={handleSubmit} className="space-y-4 p-4 rounded-lg border border-border bg-background">
            <h3 className="text-sm font-semibold text-foreground">
              {editingId ? 'Edit Webhook' : 'New Webhook'}
            </h3>

            {/* URL */}
            <div>
              <label htmlFor="webhook-url" className="block text-sm font-medium text-foreground mb-1">
                URL
              </label>
              <input
                id="webhook-url"
                type="url"
                required
                value={form.url}
                onChange={(e) => setForm((prev) => ({ ...prev, url: e.target.value }))}
                placeholder="https://example.com/webhook"
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring text-sm"
              />
            </div>

            {/* Events */}
            <div>
              <span className="block text-sm font-medium text-foreground mb-1">Events</span>
              <div className="space-y-1.5">
                {EVENT_OPTIONS.map((opt) => (
                  <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={form.events.includes(opt.value)}
                      onChange={() => handleEventToggle(opt.value)}
                      className="rounded border-input"
                    />
                    <span className="text-sm text-foreground">{opt.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Payload Template */}
            <div>
              <label htmlFor="webhook-template" className="block text-sm font-medium text-foreground mb-0.5">
                Payload Template (optional)
              </label>
              <p className="text-xs text-muted-foreground mb-1">
                Customize the JSON body sent to your endpoint
              </p>
              <textarea
                id="webhook-template"
                value={form.payloadTemplate}
                onChange={(e) => {
                  setForm((prev) => ({ ...prev, payloadTemplate: e.target.value }));
                  setTemplatePreview(null);
                }}
                placeholder={DEFAULT_TEMPLATE_PLACEHOLDER}
                rows={6}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring text-sm font-mono"
              />
              <div className="flex items-center gap-2 mt-1">
                <button
                  type="button"
                  onClick={handleValidateTemplate}
                  disabled={validating || !form.payloadTemplate.trim()}
                  className="px-3 py-1 text-xs rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
                >
                  {validating ? 'Validating...' : 'Validate & Preview'}
                </button>
              </div>
              {templatePreview && (
                <div
                  className={`mt-2 p-3 rounded-lg text-xs font-mono whitespace-pre-wrap ${
                    templatePreview.valid
                      ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                      : 'bg-destructive/10 text-destructive'
                  }`}
                >
                  {templatePreview.valid ? templatePreview.preview : templatePreview.error}
                </div>
              )}
            </div>

            {/* Content Type */}
            <div>
              <label htmlFor="webhook-content-type" className="block text-sm font-medium text-foreground mb-1">
                Content-Type
              </label>
              <input
                id="webhook-content-type"
                type="text"
                value={form.contentType}
                onChange={(e) => setForm((prev) => ({ ...prev, contentType: e.target.value }))}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring text-sm"
              />
            </div>

            {/* Secret */}
            <div>
              <label htmlFor="webhook-secret" className="block text-sm font-medium text-foreground mb-1">
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
                  className="w-full px-4 py-2 pr-16 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring text-sm"
                />
                <button
                  type="button"
                  onClick={() => setShowSecret((prev) => !prev)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showSecret ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>

            {/* Enabled */}
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm((prev) => ({ ...prev, enabled: e.target.checked }))}
                className="rounded border-input"
              />
              <span className="text-sm text-foreground">Enabled</span>
            </label>

            {/* Error messages */}
            {(createMutation.isError || updateMutation.isError) && (
              <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
                {((createMutation.error || updateMutation.error) as Error)?.message || 'Failed to save webhook'}
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={isSaving || form.events.length === 0}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm"
              >
                {isSaving ? 'Saving...' : editingId ? 'Update Webhook' : 'Create Webhook'}
              </button>
              <button
                type="button"
                onClick={resetForm}
                className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors text-sm"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default WebhooksSection;
