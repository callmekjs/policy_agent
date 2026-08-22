import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 화면과 API를 한 주소에서 쓴다. 개발 중에는 Vite가 /api를 FastAPI로 넘긴다.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
  },
})
