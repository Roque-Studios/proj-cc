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
      // Public media (blurred previews + creator profile images) mirrors the
      // nginx production proxy — direct, no /api prefix.
      '/preview': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/media': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      // Auth-gated media served directly to <img> tags (watermarked post / story
      // / message media) — mirrors the nginx production proxy.
      '/content': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/stories': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/messages': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      // Multi-page: index.html is the public creator landing page (the main
      // entry, served at / and /creator/{id}); admin.html is the creator
      // dashboard (served at /admin; settings.html stays an alias); login.html
      // is the shared sign-in page; feed/checkout/chat are the remaining pages.
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        settings: fileURLToPath(new URL('./settings.html', import.meta.url)),
        admin: fileURLToPath(new URL('./admin.html', import.meta.url)),
        login: fileURLToPath(new URL('./login.html', import.meta.url)),
        feed: fileURLToPath(new URL('./feed.html', import.meta.url)),
        checkout: fileURLToPath(new URL('./checkout.html', import.meta.url)),
        chat: fileURLToPath(new URL('./chat.html', import.meta.url)),
        profile: fileURLToPath(new URL('./profile.html', import.meta.url)),
        legal: fileURLToPath(new URL('./legal.html', import.meta.url)),
      },
    },
  },
})
