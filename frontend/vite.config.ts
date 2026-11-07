import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

const isDemo = process.env.VITE_DEMO === '1';

const cfBeacon = (token: string): Plugin => ({
  name: 'demo-cf-beacon',
  transformIndexHtml: (html) =>
    html.replace(
      '</body>',
      `<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='${JSON.stringify({ token })}'></script></body>`,
    ),
});

const pwa = () => VitePWA({
      registerType: 'prompt',
      injectRegister: false,
      workbox: {
        // Wait for the user's reload action before replacing an active bundle.
        skipWaiting: false,
        clientsClaim: true,
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        navigateFallback: '/ui/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/health/],
        runtimeCaching: [
          {
            urlPattern: /\/api\/v1\/feeds\/[^/]+\/artwork/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'artwork-cache',
              expiration: {
                maxEntries: 100,
                maxAgeSeconds: 30 * 24 * 60 * 60,
              },
            },
          },
        ],
      },
      manifest: {
        name: 'MinusPod',
        short_name: 'MinusPod',
        description: 'Podcast ad removal and feed management',
        theme_color: '#2ea8c7',
        background_color: '#f1f4f6',
        display: 'standalone',
        start_url: '/ui/',
        scope: '/ui/',
        icons: [
          {
            src: 'icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
    });

export default defineConfig({
  plugins: [
    react(),
    tailwindcss({ optimize: false }),
    ...(isDemo
      ? process.env.VITE_CF_BEACON_TOKEN ? [cfBeacon(process.env.VITE_CF_BEACON_TOKEN)] : []
      : [pwa()]),
  ],
  define: {
    __DEMO__: JSON.stringify(isDemo),
  },
  resolve: {
    alias: {
      '@': new URL('./src', import.meta.url).pathname,
    },
  },
  base: isDemo ? '/' : '/ui/',
  publicDir: isDemo ? 'demo/public' : 'public',
  experimental: isDemo ? undefined : {
    // Lazy chunk URLs resolve against the runtime prefix meta tag; HTML and CSS are rewritten server-side.
    renderBuiltUrl(filename, { hostType }) {
      if (hostType !== 'js') return undefined;
      return { runtime: `(document.querySelector('meta[name="minuspod-url-prefix"]')?.getAttribute('content') ?? '') + '/ui/' + ${JSON.stringify(filename)}` };
    },
  },
  build: {
    outDir: isDemo ? 'dist-demo' : '../static/ui',
    emptyOutDir: true,
    sourcemap: false,
    // React Query v5 + modern deps emit private class fields; es2020 default can't lower them.
    target: 'es2022',
  },
  server: {
    port: 5173,
    // The demo has no backend, and a prefix-matched /api proxy would swallow /api-docs.
    proxy: isDemo ? undefined : {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        // Dev-only: lets a dev server proxy an authenticated remote
        // backend without the browser holding that session.
        headers: process.env.VITE_PROXY_COOKIE
          ? { cookie: process.env.VITE_PROXY_COOKIE }
          : undefined,
      },
      '/health': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
