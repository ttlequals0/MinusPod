import { IS_DEMO } from '../demo/flag';
import { DEMO_BANNER } from '../demo/copy';
import { focusRing } from './fieldStyles';

const link = `inline-flex h-11 items-center px-3 font-medium underline underline-offset-2 ${focusRing}`;

export function DemoBanner() {
  if (!IS_DEMO) return null;
  return (
    <div role="note" className="bg-primary/10 text-foreground text-sm">
      <div className="safe-x safe-x-wide mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-4">
        <p className="py-2">{DEMO_BANNER}</p>
        <div className="flex items-center">
          <a href="https://docs.minuspod.app/" target="_blank" rel="noreferrer" className={link}>Docs</a>
          <a href="https://github.com/ttlequals0/MinusPod" target="_blank" rel="noreferrer" className={link}>GitHub</a>
        </div>
      </div>
    </div>
  );
}
