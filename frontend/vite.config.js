import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // Dev-mode API proxy to a locally running backend (uvicorn main:app)
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
})
