import type { QueueOrder } from '../api/types';

const QUEUE_ORDER_OPTIONS: { value: QueueOrder; label: string }[] = [
  { value: 'queued', label: 'As queued' },
  { value: 'oldest_first', label: 'Oldest first' },
  { value: 'newest_first', label: 'Newest first' },
];

export const queueOrderLabel = (order: QueueOrder): string =>
  QUEUE_ORDER_OPTIONS.find((o) => o.value === order)?.label ?? 'As queued';

// Choices the server reports, labelled here; the full list when an older server omits them.
// The current value is appended when the list lacks it, so a stored order still shows selected.
export const queueOrderChoices = (
  served?: readonly QueueOrder[], current?: QueueOrder | null,
): { value: QueueOrder; label: string }[] => {
  const label = (value: QueueOrder) => QUEUE_ORDER_OPTIONS.find((o) => o.value === value)?.label ?? value;
  if (!served?.length) return QUEUE_ORDER_OPTIONS;
  const values = current && !served.includes(current) ? [...served, current] : served;
  return values.map((value) => ({ value, label: label(value) }));
};
