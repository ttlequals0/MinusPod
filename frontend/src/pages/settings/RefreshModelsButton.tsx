import LoadingSpinner from '../../components/LoadingSpinner';
import { RefreshCw } from 'lucide-react';
import { btnBase, btnSecondary, btnGhost } from '../../components/buttonStyles';
import { DemoDisabled } from '../../components/DemoDisabled';

interface RefreshModelsButtonProps {
  onClick: () => void;
  isPending: boolean;
  // 'chip' is the section-header control; 'link' sits above a single select.
  variant?: 'chip' | 'link';
  disabled?: boolean;
}

function RefreshModelsButton({ onClick, isPending, variant = 'chip', disabled = false }: RefreshModelsButtonProps) {
  if (variant === 'link') {
    return (
      <DemoDisabled>
        <button
          type="button"
          onClick={onClick}
          disabled={isPending || disabled}
          className={`${btnBase} ${btnGhost} text-primary`}
        >
          {isPending ? 'Refreshing...' : 'Refresh models'}
        </button>
      </DemoDisabled>
    );
  }
  return (
    <DemoDisabled>
      <button
        type="button"
        onClick={onClick}
        disabled={isPending || disabled}
        className={`${btnBase} ${btnSecondary}`}
        title="Refresh model list from provider"
      >
        {isPending ? (
          <>
            <LoadingSpinner inline className="w-4 h-4" />
            Refreshing...
          </>
        ) : (
          <>
            <RefreshCw className="w-4 h-4" aria-hidden="true" />
            Refresh
          </>
        )}
      </button>
    </DemoDisabled>
  );
}

export default RefreshModelsButton;
