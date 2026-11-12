import { useState } from 'react';
import { focusRing } from './fieldStyles';
import { badgeBase, tint } from './badgeStyles';

interface Props {
  communityId: string;
  version?: number;
  protected?: boolean;
}

export function CommunityBadge({ communityId, version, protected: isProtected }: Props) {
  const [expanded, setExpanded] = useState(false);
  const short = communityId.split('-')[0];
  const text = `Community${version ? ` v${version}` : ''} - ${expanded ? communityId : short}${isProtected ? ' - protected' : ''}`;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setExpanded((v) => !v);
      }}
      className={`${badgeBase} max-w-full ${tint.teal} hover:bg-c-teal/30 transition-colors ${focusRing}`}
      title={text}
    >
      <span className="truncate">{text}</span>
    </button>
  );
}
