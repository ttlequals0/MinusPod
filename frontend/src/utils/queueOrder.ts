import type { QueueOrder } from '../api/types';

export const QUEUE_ORDER_OPTIONS: { value: QueueOrder; label: string }[] = [
  { value: 'queued', label: 'As queued' },
  { value: 'oldest_first', label: 'Oldest first' },
  { value: 'newest_first', label: 'Newest first' },
];

export const queueOrderLabel = (order: QueueOrder): string =>
  QUEUE_ORDER_OPTIONS.find((o) => o.value === order)?.label ?? 'As queued';
