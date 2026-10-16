import { useEffect, useId, useSyncExternalStore } from 'react';

const drafts = new Set<string>();
const listeners = new Set<() => void>();
export const hasUnsavedChanges = () => drafts.size > 0;
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
export const useAnyUnsavedChanges = () => useSyncExternalStore(subscribe, hasUnsavedChanges);

export function useUnsavedChanges(dirty: boolean) {
  const id = useId();
  useEffect(() => {
    if (!dirty) return;
    drafts.add(id);
    listeners.forEach(listener => listener());
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      drafts.delete(id);
      listeners.forEach(listener => listener());
      window.removeEventListener('beforeunload', beforeUnload);
    };
  }, [dirty, id]);
}
