import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

// Dev-only proxy: mirrors the nginx production setup where /api/* is forwarded
// to the FastAPI backend with the /api prefix stripped.
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        ws: true, // WebSocket upgrade for the DM realtime endpoint
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    rollupOptions: {
      // Multi-page: settings.html is the private admin page (only reachable by
      // typing its URL) and landing.html is the public creator landing page;
      // both must be emitted alongside index.html.
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        settings: fileURLToPath(new URL('./settings.html', import.meta.url)),
        landing: fileURLToPath(new URL('./landing.html', import.meta.url)),
        feed: fileURLToPath(new URL('./feed.html', import.meta.url)),
        checkout: fileURLToPath(new URL('./checkout.html', import.meta.url)),
        chat: fileURLToPath(new URL('./chat.html', import.meta.url)),
      },
    },
  },
})
