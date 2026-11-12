import { useState } from 'react';
import { focusRing } from './fieldStyles';
import { Badge } from './Badge';

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
    <Badge
      tone="teal"
      title={text}
      className={`hover:bg-c-teal/30 transition-colors ${focusRing}`}
      onClick={(e) => {
        e.stopPropagation();
        setExpanded((v) => !v);
      }}
    >
      {text}
    </Badge>
  );
}
