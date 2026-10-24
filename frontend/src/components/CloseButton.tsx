import { X } from 'lucide-react';
import { btnGhost } from './buttonStyles';
import { iconTarget, iconVisual } from './rowActionStyles';

export default function CloseButton({ onClick, label = 'Close', className }: {
  onClick: () => void;
  label?: string;
  className?: string;
}) {
  return (
    <button type="button" onClick={onClick} aria-label={label} className={`${iconTarget} ${className ?? ''}`}>
      <span className={`${iconVisual} ${btnGhost}`}>
        <X className="w-5 h-5" aria-hidden="true" />
      </span>
    </button>
  );
}
