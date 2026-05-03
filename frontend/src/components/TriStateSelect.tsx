interface TriStateSelectProps {
  /**
   * Tri-state value: true = override-on, false = override-off, null = use
   * the matching site-wide default.
   */
  value: boolean | null | undefined;
  onChange: (value: boolean | null) => void;
  id?: string;
  disabled?: boolean;
  /** Tailwind classes; falls back to the project's standard input shape. */
  className?: string;
  /** Custom labels for the three options. Defaults match per-feed override UX. */
  globalLabel?: string;
  enableLabel?: string;
  disableLabel?: string;
}

function TriStateSelect({
  value,
  onChange,
  id,
  disabled,
  className,
  globalLabel = 'Global Default',
  enableLabel = 'Enabled',
  disableLabel = 'Disabled',
}: TriStateSelectProps) {
  const stringValue =
    value === true ? 'enable' : value === false ? 'disable' : 'global';

  return (
    <select
      id={id}
      value={stringValue}
      disabled={disabled}
      onChange={(e) => {
        const v = e.target.value;
        if (v === 'enable') onChange(true);
        else if (v === 'disable') onChange(false);
        else onChange(null);
      }}
      className={
        className ??
        'w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring'
      }
    >
      <option value="global">{globalLabel}</option>
      <option value="enable">{enableLabel}</option>
      <option value="disable">{disableLabel}</option>
    </select>
  );
}

export default TriStateSelect;
