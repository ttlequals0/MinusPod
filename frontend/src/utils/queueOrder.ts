import type { QueueOrder } from '../api/types';

export const QUEUE_ORDER_OPTIONS: { value: QueueOrder; label: string }[] = [
  { value: 'queued', label: 'As queued' },
  { value: 'oldest_first', label: 'Oldest first' },
  { value: 'newest_first', label: 'Newest first' },
];

export const queueOrderLabel = (order: QueueOrder): string =>
  QUEUE_ORDER_OPTIONS.find((o) => o.value === order)?.label ?? 'As queued';

// Choices the server reports, labelled here; the full list when an older server omits them.
export const queueOrderChoices = (served?: readonly QueueOrder[]): { value: QueueOrder; label: string }[] =>
  served?.length
    ? served.map((value) => ({ value, label: QUEUE_ORDER_OPTIONS.find((o) => o.value === value)?.label ?? value }))
    : QUEUE_ORDER_OPTIONS;
