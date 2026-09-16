import LoadingSpinner from '../../components/LoadingSpinner';
import { btnSecondary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';

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
        className={`text-sm text-primary hover:underline disabled:opacity-50 ${focusRing}`}
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
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
      title="Refresh model list from provider"
    >
      {isPending ? (
        <>
          <LoadingSpinner inline className="w-3.5 h-3.5" />
          Refreshing...
        </>
      ) : (
        <>
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Refresh
        </>
      )}
    </button>
  );
}

export default RefreshModelsButton;
