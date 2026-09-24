import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    build: {
      // Vite 默认按 baseline-widely-available 压缩 CSS：会把 @media (max-width) 改写成
      // Media Queries L4 的区间语法 width<=Npx（iOS 16.4 才支持），并删掉 height:100dvh
      // 前的 height:100vh 回退（dvh 要 iOS 15.4）。而 iOS 工程的部署目标是 15.0，
      // 落在 15.0~16.3 的设备上全部断点失效、外壳高度归零，界面直接退化成桌面布局。
      // 锁死 CSS 目标到 safari15，压缩器就会保留 max-width 写法与新单位的回退声明。
      cssTarget: 'safari15'
    },
    server: {
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true
        },
        '/minio': {
          target: 'http://minio:9000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/minio/, '')
        }
      },
      watch: {
        usePolling: true,
        ignored: ['**/node_modules/**', '**/dist/**'],
      },
      host: '0.0.0.0',
    }
  }
})
