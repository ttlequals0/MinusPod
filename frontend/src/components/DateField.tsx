import { useEffect, useRef, useState } from 'react';
import { inputBase, filterLabel, filterControl } from './fieldStyles';

interface DateFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
}

function DateField({
  id, label, value, onChange, min, max,
}: DateFieldProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing && inputRef.current && inputRef.current.value !== value) {
      inputRef.current.value = value;
    }
  }, [editing, value]);
  return (
    <div className="min-w-0">
      <label htmlFor={id} className={filterLabel}>{label}</label>
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          type="date"
          id={id}
          min={min}
          max={max}
          defaultValue={value}
          onFocus={() => setEditing(true)}
          onChange={(event) => onChange(event.target.value)}
          onBlur={() => {
            setEditing(false);
            onChange(inputRef.current?.value ?? '');
          }}
          className={`${filterControl} ${inputBase} max-sm:px-0 max-sm:text-base [&::-webkit-date-and-time-value]:text-left [&::-webkit-datetime-edit]:min-w-0 [&::-webkit-datetime-edit]:p-0`}
        />
      </div>
    </div>
  );
}

export default DateField;
