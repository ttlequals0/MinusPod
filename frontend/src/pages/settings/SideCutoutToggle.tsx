import { useEffect, useState } from 'react';
import ToggleSwitch from '../../components/ToggleSwitch';
import { measureSideInset, readSideCutoutFill, setSideCutoutFill } from '../../utils/sideCutout';

function SideCutoutToggle() {
  const [on, setOn] = useState(readSideCutoutFill);
  const [inset, setInset] = useState(0);

  useEffect(() => {
    const measure = () => setInset(measureSideInset());
    measure();
    window.addEventListener('resize', measure);
    window.addEventListener('orientationchange', measure);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('orientationchange', measure);
    };
  }, []);

  if (inset <= 0 && !on) return null;

  return (
    <div className="mt-6 border-t border-border pt-4">
      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={on}
          onChange={(v) => { setSideCutoutFill(v); setOn(v); }}
          ariaLabel="Use full width beside a side cutout"
        />
        <span className="text-sm font-medium text-foreground">Use full width beside a side cutout</span>
      </label>
      <p className="mt-2 text-sm text-muted-foreground sm:ml-14">
        On this device only. Page content extends into the area beside a camera or screen cutout. Turn it off if anything ends up hidden.
      </p>
    </div>
  );
}

export default SideCutoutToggle;
