import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import path from 'path';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      base: '/ui/',
      scope: '/ui/',
      workbox: {
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
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  base: '/ui/',
  build: {
    outDir: '../static/ui',
    emptyOutDir: true,
    sourcemap: false,
    // React Query v5 + modern deps emit private class fields; es2020 default can't lower them.
    target: 'es2022',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
