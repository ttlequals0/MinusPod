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
      className="px-2 py-0.5 text-xs rounded bg-c-teal/15 text-c-teal hover:bg-c-teal/25 transition-colors"
      title="Community pattern"
    >
      community{version ? ` v${version}` : ''} · {expanded ? communityId : short}
      {isProtected ? ' · protected' : ''}
    </button>
  );
}
