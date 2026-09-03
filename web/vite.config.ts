import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 代理：后端 API 与健康检查转发到本地 FastAPI（uvicorn main:app --port 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/user': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/chat': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ask': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/feedback': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/kb/overview': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/kb/catalog': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/admin': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/audit': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
