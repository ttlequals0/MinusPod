import { useQuery } from '@tanstack/react-query';
import { getDetections } from '../../api/detections';
import LoadingSpinner from '../../components/LoadingSpinner';

export default function AdReviewTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', { page: 1 }],
    queryFn: () => getDetections({ page: 1 }),
  });

  if (isLoading) return <LoadingSpinner />;
  if (error) {
    return (
      <div className="text-destructive text-sm">
        Failed to load detections.
      </div>
    );
  }
  if (!data || data.total === 0) {
    return (
      <div className="text-muted-foreground text-sm py-8 text-center">
        No detections need review.
      </div>
    );
  }
  return <div>{data.total} detections</div>;
}
