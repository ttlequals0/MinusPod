import LoadingSpinner from '../../components/LoadingSpinner';
import { RefreshCw } from 'lucide-react';
import { btnBase, btnSecondary, btnGhost } from '../../components/buttonStyles';

interface RefreshModelsButtonProps {
  onClick: () => void;
  isPending: boolean;
  // 'chip' is the section-header control; 'link' sits above a single select.
  variant?: 'chip' | 'link';
}

function RefreshModelsButton({ onClick, isPending, variant = 'chip' }: RefreshModelsButtonProps) {
  if (variant === 'link') {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={isPending}
        className={`${btnBase} ${btnGhost} text-primary`}
      >
        {isPending ? 'Refreshing...' : 'Refresh models'}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isPending}
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
  );
}

export default RefreshModelsButton;
