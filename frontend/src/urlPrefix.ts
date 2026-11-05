// Runtime reverse-proxy prefix injected into index.html by the server; empty when served at the root.
export const URL_PREFIX = document.querySelector('meta[name="minuspod-url-prefix"]')?.getAttribute('content') ?? '';
export const UI_BASE = `${URL_PREFIX}/ui`;
export const API_BASE = `${URL_PREFIX}/api/v1`;
export const uiUrl = (path: string) => `${UI_BASE}${path}`;
export const apiUrl = (path: string) => `${API_BASE}${path}`;
