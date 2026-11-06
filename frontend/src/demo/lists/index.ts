import type { Fixture } from '../fixtures';

export type ListHandler = (url: URL, load: (key: string) => Promise<Fixture | null>) => Promise<Fixture | null>;

export const registry: Array<{ pattern: RegExp; handler: ListHandler }> = [];

export function matchList(pathname: string): ListHandler | null {
  for (const entry of registry) {
    if (entry.pattern.test(pathname)) return entry.handler;
  }
  return null;
}
