// Down caret that flips to point up while its section is open. Shared by the
// hand-rolled collapsible headers; row expanders use DisclosureButton.
function ChevronCaret({ expanded, className = 'w-4 h-4' }: { expanded: boolean; className?: string }) {
  return (
    <svg
      className={`${className} text-muted-foreground transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
      fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  );
}

export default ChevronCaret;
