import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// AI engine (FastAPI) default port; see .env.example → AI_ENGINE_PORT
const AI_ENGINE_TARGET = 'http://127.0.0.1:8100'

export default defineConfig({
  // Relative asset URLs so the SPA works under any gateway prefix
  // (e.g. /apps/factorymind/) without a sub-path rewrite.
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: AI_ENGINE_TARGET,
        changeOrigin: true,
      },
    },
  },
})