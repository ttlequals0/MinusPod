import { X } from 'lucide-react';
import { btnGhost } from './buttonStyles';
import { iconTarget, iconVisual } from './rowActionStyles';

export default function CloseButton({ onClick, label = 'Close', className, disabled = false }: {
  onClick: () => void;
  label?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <button type="button" onClick={onClick} aria-label={label} disabled={disabled}
      className={`${iconTarget} disabled:opacity-50 ${className ?? ''}`}>
      <span className={`${iconVisual} ${btnGhost}`}>
        <X className="w-5 h-5" aria-hidden="true" />
      </span>
    </button>
  );
}
