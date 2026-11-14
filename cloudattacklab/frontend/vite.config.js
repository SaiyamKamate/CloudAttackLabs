import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    // The project lives on the Windows D: drive, edited through WSL. Native file
    // events from /mnt/d do not reach the Linux inotify API, so Vite never sees
    // saved files. Polling makes the watcher check for changes itself.
    watch: { usePolling: true, interval: 150 },
    // In development the dashboard is served from :5173 and the backend from
    // :8000. Calls to /api are forwarded to the backend, so the browser only
    // ever talks to one origin (no CORS, and api.js uses relative paths).
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
