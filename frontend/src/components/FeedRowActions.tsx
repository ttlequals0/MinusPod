import { RefreshCw, Trash2 } from 'lucide-react';
import DropdownMenu from './DropdownMenu';
import { DemoDisabled } from './DemoDisabled';
import { btnBase, btnPrimaryOutline, btnDestructiveOutline } from './buttonStyles';

interface FeedRefreshMenuProps {
  slug: string;
  onRefresh: (slug: string, options?: { force?: boolean }) => void;
  isRefreshing?: boolean;
  compact?: boolean;
}

// compact is the icon-first list row; otherwise the text trigger used on cards.
export function FeedRefreshMenu({ slug, onRefresh, isRefreshing, compact }: FeedRefreshMenuProps) {
  return (
    <DemoDisabled>
      <DropdownMenu
        triggerLabel={compact ? (
          <>
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            <span className="hidden sm:inline">{isRefreshing ? 'Refreshing' : 'Refresh'}</span>
          </>
        ) : (isRefreshing ? 'Refreshing...' : 'Refresh')}
        triggerClassName={`${btnBase} ${btnPrimaryOutline}`}
        disabled={isRefreshing}
        title={compact && isRefreshing ? 'Refreshing' : 'Refresh feed'}
        chevronClassName={compact ? 'w-3 h-3' : undefined}
        items={[
          {
            title: 'Refresh',
            subtitle: 'Check for new episodes',
            onClick: () => onRefresh(slug),
          },
          {
            title: 'Force refresh',
            subtitle: 'Bypass cache',
            onClick: () => onRefresh(slug, { force: true }),
          },
        ]}
      />
    </DemoDisabled>
  );
}

interface FeedDeleteButtonProps {
  slug: string;
  onDelete: (slug: string) => void;
  iconClassName: string;
}

export function FeedDeleteButton({ slug, onDelete, iconClassName }: FeedDeleteButtonProps) {
  return (
    <DemoDisabled>
      <button
        onClick={() => onDelete(slug)}
        className={`${btnBase} ${btnDestructiveOutline}`}
        title="Delete feed"
        aria-label="Delete feed"
      >
        <Trash2 className={iconClassName} aria-hidden="true" />
        <span className="hidden sm:inline">Delete</span>
      </button>
    </DemoDisabled>
  );
}
