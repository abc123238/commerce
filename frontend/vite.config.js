import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发：vite dev server 把 /api 代理到后端（后端在 8000）
// 生产：vite build 产物直接输出到 app/static，由 FastAPI 静态托管 + Docker COPY app 带上
export default defineConfig({
  plugins: [vue()],
  base: '/',
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../app/static',
    emptyOutDir: true,
  },
})
