import { useMemo } from 'react';
import { useSearchParams } from 'react-router';

// List state kept in the query string: values equal to the default are left out.
// Pass a module-level defaults object so the returned functions stay stable.
export function useUrlParams<K extends string>(defaults: Record<K, string>) {
  const [params, setParams] = useSearchParams();
  return useMemo(() => {
    const read = (key: K) => params.get(key) ?? defaults[key];
    const readPage = (key: K) => Math.max(1, Number(read(key)) || 1);
    // One write per interaction: a second call would build from the stale URL.
    const write = (changes: Partial<Record<K, string>>) => {
      const next = new URLSearchParams(params);
      for (const key of Object.keys(changes) as K[]) {
        const value = changes[key];
        if (value && value !== defaults[key]) next.set(key, value);
        else next.delete(key);
      }
      setParams(next, { replace: true });
    };
    return { read, readPage, write };
  }, [params, setParams, defaults]);
}
