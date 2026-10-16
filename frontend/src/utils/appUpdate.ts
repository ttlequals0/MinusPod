import { useSyncExternalStore } from 'react';

let apply: (() => void) | null = null;
const listeners = new Set<() => void>();
export function offerAppUpdate(update: () => void) {
  apply = update;
  listeners.forEach(listener => listener());
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
export function useAppUpdate() {
  return useSyncExternalStore(subscribe, () => apply);
}
