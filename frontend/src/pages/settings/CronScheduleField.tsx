import { focusRing } from '../../components/fieldStyles';

interface CronScheduleFieldProps {
  id: string;
  value: string;
  onChange: (cron: string) => void;
  placeholder: string;
}

/** "Schedule (cron):" input shared by the scheduled-job cards; times are UTC. */
export default function CronScheduleField({ id, value, onChange, placeholder }: CronScheduleFieldProps) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <label htmlFor={id} className="text-sm text-muted-foreground whitespace-nowrap">
        Schedule (cron):
      </label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`w-40 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground font-mono text-sm ${focusRing}`}
      />
      <span className="text-xs text-muted-foreground">UTC</span>
    </div>
  );
}
