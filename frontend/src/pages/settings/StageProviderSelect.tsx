import { SAME_AS_DETECTION, SLOT_LABELS, SLOT_PRIMARY, SLOT_SECONDARY } from '../../api/types';
import { selectBase } from '../../components/fieldStyles';

export interface SlotOption {
  value: string;
  label: string;
}

// Secondary is listed only while the secondary provider is configured and enabled.
export function detectionSlotOptions(secondaryEnabled: boolean): SlotOption[] {
  return [
    { value: SLOT_PRIMARY, label: SLOT_LABELS.primary },
    ...(secondaryEnabled ? [{ value: SLOT_SECONDARY, label: SLOT_LABELS.secondary }] : []),
  ];
}

export function inheritedSlotOptions(secondaryEnabled: boolean): SlotOption[] {
  return [{ value: SAME_AS_DETECTION, label: 'Same as detection' }, ...detectionSlotOptions(secondaryEnabled)];
}

interface StageProviderSelectProps {
  id: string;
  label: string;
  value: string;
  options: SlotOption[];
  onChange: (slot: string) => void;
  secondaryEnabled: boolean;
}

function StageProviderSelect({ id, label, value, options, onChange, secondaryEnabled }: StageProviderSelectProps) {
  // A stage saved on the secondary slot before it was turned off keeps that
  // value; hiding the option would misrepresent what is actually stored.
  const strandedOnSecondary = value === SLOT_SECONDARY && !secondaryEnabled;
  const shownOptions = strandedOnSecondary
    ? [...options, { value: SLOT_SECONDARY, label: `${SLOT_LABELS.secondary} (off)` }]
    : options;
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-foreground mb-2">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full ${selectBase}`}
      >
        {shownOptions.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {strandedOnSecondary && (
        <p className="mt-1 text-sm text-warning">
          {SLOT_LABELS.secondary} is off, so this stage runs on {SLOT_LABELS.primary}.
        </p>
      )}
    </div>
  );
}

export default StageProviderSelect;
