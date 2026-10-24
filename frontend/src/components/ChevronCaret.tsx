import { ChevronDown } from 'lucide-react';

// Down caret that flips to point up while its section is open. Shared by the
// hand-rolled collapsible headers; row expanders use DisclosureButton.
function ChevronCaret({ expanded, className = 'w-4 h-4' }: { expanded: boolean; className?: string }) {
  return (
    <ChevronDown
      className={`${className} text-muted-foreground transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
      aria-hidden="true"
    />
  );
}

export default ChevronCaret;
