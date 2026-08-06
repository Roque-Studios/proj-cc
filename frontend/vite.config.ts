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
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    rollupOptions: {
      // Multi-page: settings.html is the private admin page (only reachable by
      // typing its URL) and must be emitted alongside index.html.
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        settings: fileURLToPath(new URL('./settings.html', import.meta.url)),
      },
    },
  },
})
