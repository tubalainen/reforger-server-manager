import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      // Two pages from one build: the manager, and the Server Supervisor (#204),
      // which the same image serves from its own compose file.
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        supervisor: fileURLToPath(new URL('./supervisor.html', import.meta.url)),
      },
    },
  },
  server: {
    // Dev-mode API proxy to a locally running backend (uvicorn main:app)
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
})
