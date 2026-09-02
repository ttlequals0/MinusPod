import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import { getSettings, updateSettings } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { btnPrimary } from '../../components/buttonStyles';
import { focusRing, inputBase } from '../../components/fieldStyles';
import FieldResetHeader from './FieldResetHeader';
import SavedBadge from './SavedBadge';

const STORAGE_KEY = 'settings-section-outbound-requests';

type FieldKey = 'downloadUserAgent' | 'feedUserAgent';

const FIELDS: { key: FieldKey; label: string; help: string }[] = [
  {
    key: 'downloadUserAgent',
    label: 'Audio, artwork, and chapters',
    help: 'Sent as the User-Agent when downloading media. Some hosts refuse '
      + 'browser identifiers older than a version they consider current, which '
      + 'shows up as a 403 on download. Pasting a current browser string clears it.',
  },
  {
    key: 'feedUserAgent',
    label: 'RSS feeds',
    help: 'Sent as the User-Agent when fetching feeds. Some feed hosts answer '
      + 'only a declared podcast client and reject browser strings, so keep this '
      + 'one an honest application identifier.',
  },
];

function OutboundRequestsSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: getSettings });

  const [draft, setDraft] = useState<Partial<Record<FieldKey, string>>>({});
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (payload: Partial<Record<FieldKey, string>>) => updateSettings(payload),
    onSuccess: () => {
      setError(null);
      setDraft({});
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: (e: unknown) => setError(getErrorMessage(e, 'Save failed')),
  });

  const stored = (key: FieldKey) => settings?.[key]?.value ?? '';
  const isDefault = (key: FieldKey) => settings?.[key]?.isDefault !== false;
  const current = (key: FieldKey) => draft[key] ?? stored(key);
  const dirty = FIELDS.some(({ key }) => current(key) !== stored(key));

  return (
    <CollapsibleSection
      title="Outbound Requests"
      subtitle="The User-Agent MinusPod sends when it fetches feeds, audio, and artwork."
      storageKey={STORAGE_KEY}
    >
      <div className="space-y-6">
        {FIELDS.map(({ key, label, help }) => (
          <div key={key}>
            <FieldResetHeader
              htmlFor={key}
              label={label}
              isDefault={isDefault(key)}
              resetAriaLabel={`Reset ${label} User-Agent`}
              onReset={() => save.mutate({ [key]: '' })}
            />
            <input
              type="text"
              id={key}
              value={current(key)}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              spellCheck={false}
              autoComplete="off"
              maxLength={512}
              className={`w-full font-mono ${inputBase}`}
            />
            <p className="mt-1 text-sm text-muted-foreground">{help}</p>
          </div>
        ))}

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => save.mutate(draft)}
            disabled={save.isPending || !dirty}
            className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 text-sm ${focusRing}`}
          >
            {save.isPending ? 'Saving...' : 'Save'}
          </button>
          {save.isSuccess && <SavedBadge className="ml-1" />}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default OutboundRequestsSection;
