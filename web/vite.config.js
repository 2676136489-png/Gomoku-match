import { realpathSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 这个项目目录挂在 Windows 目录联接（junction）下面，
// process.cwd() 拿到的是 C:\ 前缀，而 node 内部解析成 D:\ 真实路径。
// 两边前缀不一致时 rollup 算不出相对路径，构建会报 fileName 非法。
// 所以 root 一律用解引用之后的真实路径。
const root = realpathSync(fileURLToPath(new URL('.', import.meta.url)))

export default defineConfig({
  root,
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    // 产物目录叫 site 而不是默认的 dist：云端发布沙箱会把 dist/build/out 当成
    // 构建输出从上传包里剔除，导致服务起来了但没有页面。目录名与 server/db.py
    // 的 WEB_DIST 必须一致。
    outDir: path.join(root, 'site'),
    emptyOutDir: true,
  },
})
