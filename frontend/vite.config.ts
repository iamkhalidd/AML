import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// The API lives on another host. In development Vite forwards /v1 there, the same way
// Vercel's rewrite does in production, so the session cookie stays same-origin.
const apiTarget = process.env.AML_API_URL ?? 'https://aml-demo.onrender.com'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), vueDevTools()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/v1': { target: apiTarget, changeOrigin: true, secure: true },
    },
  },
})
