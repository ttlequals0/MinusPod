import LoadingSpinner from './LoadingSpinner';

interface CatalogStatusProps {
  loading?: boolean;
  error?: boolean;
  /** Message from a failed Refresh, shown in place of the generic fetch line. */
  refreshError?: string | null;
}

/** Fetch state under a model select: loading, a refresh failure, or a failed load. */
function CatalogStatus({ loading = false, error = false, refreshError = null }: CatalogStatusProps) {
  if (loading) {
    return (
      <p className="mt-1 text-sm text-muted-foreground flex items-center gap-1.5">
        <LoadingSpinner inline className="w-3.5 h-3.5" />
        Loading models...
      </p>
    );
  }
  if (refreshError) return <p className="mt-1 text-sm text-warning">{refreshError}</p>;
  if (error) {
    return (
      <p className="mt-1 text-sm text-warning">
        Could not load this provider's model list. Refresh to try again.
      </p>
    );
  }
  return null;
}

export default CatalogStatus;
