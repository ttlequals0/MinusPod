const RING = 'border-2 border-muted border-t-primary rounded-full animate-spin';

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  inline?: boolean;
}

function LoadingSpinner({ size = 'md', className = '', inline = false }: LoadingSpinnerProps) {
  const sizes = {
    sm: 'w-4 h-4',
    md: 'w-8 h-8',
    lg: 'w-12 h-12',
  };

  // Inline mode renders a span: it is used inside phrasing content (a <p>),
  // where a div is invalid nesting.
  if (inline) {
    return (
      <span
        className={`inline-block ${className || sizes[size]} ${RING}`}
      />
    );
  }

  return (
    <div className={`flex justify-center items-center ${className}`}>
      <div
        className={`${sizes[size]} ${RING}`}
      />
    </div>
  );
}

export default LoadingSpinner;
