import { useState } from 'react';
import type { SystemOneCredentialSlot, SystemOneProfile, SystemOneProfiles, SystemOneProvider, UpdateSettingsPayload } from '../../api/types';
import { SLOT_LABELS } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import { btnPrimary, touchTarget } from '../../components/buttonStyles';
import { focusRing, inputBase, selectBase } from '../../components/fieldStyles';
import NumberInput from '../../components/NumberInput';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import { SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS } from '../../utils/segmentCategory';
import SavedBadge from './SavedBadge';

type ProfilePatch = Partial<SystemOneProfile>;
type DraftProfiles = Record<SystemOneCredentialSlot, Record<SystemOneProvider, SystemOneProfile>>;

interface Props {
  connections: Record<SystemOneCredentialSlot, string>;
  profiles: SystemOneProfiles;
  defaults: SystemOneProfiles;
  isDefault: Record<SystemOneCredentialSlot, Record<SystemOneProvider, boolean>>;
  onSave: (payload: UpdateSettingsPayload) => Promise<unknown>;
  pending: boolean;
  error: string | null;
}

const slots: SystemOneCredentialSlot[] = ['primary', 'secondary'];
const providers: SystemOneProvider[] = ['typesafe', 'systemone-compatible'];

function equalProfiles(a: DraftProfiles, b: SystemOneProfiles) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function SystemOneTunablesSection({ connections, profiles, defaults, isDefault, onSave, pending, error }: Props) {
  const [selectedSlot, setSelectedSlot] = useState<SystemOneCredentialSlot>('primary');
  const [draft, setDraft] = useState<DraftProfiles>(profiles);
  const [baseline, setBaseline] = useState(profiles);
  if (profiles !== baseline) {
    if (equalProfiles(draft, baseline)) setDraft(profiles);
    setBaseline(profiles);
  }
  const dirty = !equalProfiles(draft, profiles);

  const change = <K extends keyof SystemOneProfile>(
    slot: SystemOneCredentialSlot,
    provider: SystemOneProvider,
    field: K,
    value: SystemOneProfile[K],
  ) => setDraft((current) => ({
    ...current,
    [slot]: {
      ...current[slot],
      [provider]: { ...current[slot][provider], [field]: value },
    },
  }));

  const save = async () => {
    const patch: Partial<Record<SystemOneCredentialSlot, Partial<Record<SystemOneProvider, ProfilePatch>>>> = {};
    for (const slot of slots) {
      for (const provider of providers) {
        const changed: ProfilePatch = {};
        for (const key of Object.keys(draft[slot][provider]) as (keyof SystemOneProfile)[]) {
          if (draft[slot][provider][key] !== profiles[slot][provider][key]) {
            (changed as Record<string, unknown>)[key] = draft[slot][provider][key];
          }
        }
        if (Object.keys(changed).length) {
          patch[slot] ??= {};
          patch[slot]![provider] = changed;
        }
      }
    }
    try {
      await onSave({ systemOneTunables: patch });
    } catch {
      // The parent mutation renders the error.
    }
  };

  const resetProfile = async (slot: SystemOneCredentialSlot, provider: SystemOneProvider) => {
    const next = { ...draft, [slot]: { ...draft[slot], [provider]: defaults[slot][provider] } };
    setDraft(next);
    try {
      await onSave({ systemOneTunables: { [slot]: { [provider]: null } } });
    } catch {
      // The parent mutation renders the error.
    }
  };

  const editableSlots = slots.filter((slot) => providers.includes(connections[slot] as SystemOneProvider));
  const selectedProfileSlot = editableSlots.includes(selectedSlot) ? selectedSlot : editableSlots[0];

  return (
    <CollapsibleSection title="System One">
      <div className="space-y-5">
        {!selectedProfileSlot ? (
          <p className="text-sm text-muted-foreground">Choose a System One provider in LLM Provider.</p>
        ) : <>
        <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          Chapters and pattern cleanup are unavailable for System One models.
        </div>
        {editableSlots.length > 1 && (
          <div>
            <label htmlFor="system-one-provider" className="mb-2 block text-sm font-medium text-foreground">Provider</label>
            <select id="system-one-provider" value={selectedProfileSlot} onChange={(event) => setSelectedSlot(event.target.value as SystemOneCredentialSlot)} className={`w-full min-h-11 ${selectBase}`}>
              {editableSlots.map((slot) => <option key={slot} value={slot}>{SLOT_LABELS[slot]}</option>)}
            </select>
          </div>
        )}
          {(() => {
            const slot = selectedProfileSlot;
            const provider = connections[slot] as SystemOneProvider;
            const value = draft[slot][provider];
            const id = `${slot}-${provider}`;
            const number = <K extends keyof SystemOneProfile>(field: K, label: string, min: number, step: number | 'any' = 1, nullable = false) => {
              const fieldProps = {
                id: `${id}-${String(field)}`,
                min,
                max: ['detectionEnter', 'detectionStay', 'reviewEvidenceEnter', 'reviewChoiceEnter', 'reviewProgrammeVeto'].includes(field) ? 1 : undefined,
                step,
                disabled: field === 'maxChoiceOptions' && provider === 'typesafe',
                className: `w-full min-h-11 ${inputBase}`,
              };
              return (
              <div>
                <label htmlFor={`${id}-${String(field)}`} className="mb-1 block text-sm font-medium text-foreground">{label}</label>
                {nullable ? (
                  <DraftNumberInput {...fieldProps} value={value[field] as number | null} fallback={null} parse={parseOptionalNumber} onChange={(next) => change(slot, provider, field, next as SystemOneProfile[K])} />
                ) : (
                  <NumberInput {...fieldProps} value={value[field] as number} fallback={defaults[slot][provider][field] as number} onCommit={(next) => change(slot, provider, field, next as SystemOneProfile[K])} />
                )}
                {field === 'maxChoiceOptions' && provider === 'typesafe' && <p className="mt-1 text-xs text-muted-foreground">TypeSafe has a fixed maximum of 255.</p>}
                {nullable && <p className="mt-1 text-xs text-muted-foreground">
                  {field === 'reviewEvidenceEnter' || field === 'reviewChoiceEnter'
                    ? 'Blank uses the detection confidence threshold.'
                    : field === 'maxQuestionsPerRequest'
                      ? 'Blank removes the question-count limit.'
                      : field === 'maxRequestBytes'
                        ? 'Blank removes the request-size limit.'
                        : 'Blank removes the Choice option limit.'}
                </p>}
              </div>
              );
            };
            const toggle = (field: 'categoryPass' | 'refineBoundaries', label: string) => (
              <label className="flex min-h-11 items-center gap-3 text-sm text-foreground">
                <input type="checkbox" checked={value[field]} onChange={(event) => change(slot, provider, field, event.target.checked)} className="h-5 w-5 accent-primary" />
                {label}
              </label>
            );
            return (
              <div key={id} className="space-y-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    {isDefault[slot][provider] && <p className="mt-1 text-xs text-muted-foreground">Using defaults</p>}
                  </div>
                  <button type="button" className={`px-2 py-1 text-xs underline ${touchTarget}`} disabled={pending} onClick={() => void resetProfile(slot, provider)}>Reset profile</button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {number('detectionEnter', 'Detection confidence threshold', 0, 0.01)}
                  {number('detectionStay', 'Minimum confidence to continue a segment', 0, 0.01)}
                </div>
                {toggle('categoryPass', 'Run ad category classification')}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {number('categoryContext', 'Neighboring segments for category context', 0)}
                <div>
                  <label htmlFor={`${id}-defaultCategory`} className="mb-1 block text-sm font-medium text-foreground">Default ad category</label>
                  <select id={`${id}-defaultCategory`} value={value.defaultCategory} onChange={(event) => change(slot, provider, 'defaultCategory', event.target.value)} className={`w-full min-h-11 ${selectBase}`}>
                    {SEGMENT_CATEGORIES.map((category) => <option key={category} value={category}>{SEGMENT_CATEGORY_LABELS[category]}</option>)}
                  </select>
                </div>
                </div>
                {toggle('refineBoundaries', 'Refine ad boundaries')}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {number('requestDeadlineSeconds', 'Logical request deadline (seconds)', Number.MIN_VALUE, 'any')}
                {number('maxConcurrentOperations', 'Maximum concurrent operations', 1)}
                {number('retryAfterMaxSeconds', 'Maximum Retry-After wait (seconds)', 0, 'any')}
                </div>
                <div className="border-t border-border pt-3 space-y-3">
                  <h4 className="text-sm font-medium text-foreground">Review thresholds</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {number('reviewEvidenceEnter', 'Evidence enter threshold', 0, 0.01, true)}
                  {number('reviewChoiceEnter', 'Choice enter threshold', 0, 0.01, true)}
                  {number('reviewProgrammeVeto', 'Programme match veto threshold', 0, 0.01)}
                  {number('reviewBoundaryCapSeconds', 'Maximum boundary change (seconds)', Number.MIN_VALUE, 'any')}
                  {number('reviewContextSeconds', 'Review context (seconds)', Number.MIN_VALUE, 'any')}
                  </div>
                </div>
                <div className="border-t border-border pt-3 space-y-3">
                  <h4 className="text-sm font-medium text-foreground">Protocol limits</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {number('maxQuestionsPerRequest', 'Questions per API request', 1, 1, true)}
                  {number('maxRequestBytes', 'Request size limit (bytes)', 1, 1, true)}
                  {number('maxChoiceOptions', 'Maximum Choice options', 1, 1, provider === 'systemone-compatible')}
                  </div>
                </div>
              </div>
            );
          })()}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="flex items-center gap-3">
          <button type="button" className={`px-4 py-2 rounded-lg ${btnPrimary} ${touchTarget} disabled:opacity-50 ${focusRing}`} disabled={!dirty || pending} onClick={() => void save()}>
            {pending ? 'Saving...' : 'Save System One tuning'}
          </button>
          {!dirty && !pending && <SavedBadge />}
        </div>
        </>}
      </div>
    </CollapsibleSection>
  );
}

export default SystemOneTunablesSection;
