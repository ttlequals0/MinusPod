import { useState } from 'react';
import type { ReactNode } from 'react';
import type { ClaudeModel } from '../../api/types';
import type { ModelCatalog } from '../../hooks/useModelCatalog';
import CatalogStatus from '../../components/CatalogStatus';
import { formatModelLabel } from './settingsUtils';
import { focusRing, selectBase } from '../../components/fieldStyles';

interface ModelSelectProps {
  id: string;
  label: string;
  value: string;
  catalog: ModelCatalog;
  onChange: (model: string) => void;
  description?: ReactNode;
  // Labels the blank value when blank means "inherit" rather than unset.
  inheritLabel?: string;
}

// A saved model id the catalog does not list, or no catalog at all, would
// render the <select> blank, which users read as "the setting was reset".
function renderOrphan(value: string, models: ClaudeModel[] | undefined) {
  if (!value) return null;
  if (!models) return <option value={value}>{value}</option>;
  if (models.some((m) => m.id === value)) return null;
  return <option value={value}>{value} (current, not in catalog)</option>;
}

function ModelSelect({ id, label, value, catalog, onChange, description, inheritLabel }: ModelSelectProps) {
  // Free-text toggle lets proxies, private deployments and new model ids in
  // despite the catalog only listing what the provider advertises.
  const [typed, setTyped] = useState(false);
  const notConfigured = !value && !inheritLabel;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <label htmlFor={id} className="block text-sm font-medium text-foreground">
          {label}
        </label>
        <button
          type="button"
          onClick={() => setTyped(!typed)}
          className={`text-xs text-primary hover:underline transition-colors rounded ${focusRing}`}
        >
          {typed ? 'Choose from list' : 'Type a model ID'}
        </button>
      </div>
      {typed ? (
        <input
          type="text"
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={inheritLabel ? `Blank: ${inheritLabel.toLowerCase()}` : "Provider's exact model ID"}
          spellCheck={false}
          autoComplete="off"
          className={`w-full min-h-[44px] px-3 py-2 rounded-lg border border-input bg-background text-foreground text-sm ${focusRing}`}
        />
      ) : (
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`w-full ${selectBase}`}
        >
          {inheritLabel && <option value="">{inheritLabel}</option>}
          {notConfigured && <option value="">Not configured</option>}
          {renderOrphan(value, catalog.models)}
          {catalog.models?.map((model) => (
            <option key={model.id} value={model.id}>
              {formatModelLabel(model)}
            </option>
          ))}
        </select>
      )}
      {notConfigured && (
        <p className="mt-1 text-sm text-muted-foreground">Pick a model before processing episodes.</p>
      )}
      <CatalogStatus loading={catalog.isLoading} error={catalog.isError} />
      {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}

export default ModelSelect;
