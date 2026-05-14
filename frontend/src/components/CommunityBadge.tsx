import { useState } from 'react';

interface Props {
  communityId: string;
  version?: number;
  protected?: boolean;
}

export function CommunityBadge({ communityId, version, protected: isProtected }: Props) {
  const [expanded, setExpanded] = useState(false);
  const short = communityId.split('-')[0];
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setExpanded((v) => !v);
      }}
      className="px-2 py-0.5 text-xs rounded bg-teal-500/15 text-teal-700 dark:text-teal-400 hover:bg-teal-500/25 transition-colors"
      title="Community pattern"
    >
      community{version ? ` v${version}` : ''} · {expanded ? communityId : short}
      {isProtected ? ' · protected' : ''}
    </button>
  );
}
