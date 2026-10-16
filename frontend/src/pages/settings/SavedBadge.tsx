// Green "Saved" confirmation shown next to a section's Save button.
function SavedBadge({ className = '' }: { className?: string }) {
  return <span role="status" className={`text-sm text-success${className ? ` ${className}` : ''}`}>Saved</span>;
}

export default SavedBadge;
