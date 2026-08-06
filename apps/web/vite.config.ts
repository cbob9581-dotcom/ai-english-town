import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// exFAT 不支持目录符号链接/junction，pnpm 的 workspace:* 无法安装到 node_modules。
// 用别名把工作区包直接解析到 TS 源码（Vite/esbuild 可直接转译），等效于 brief 的
// `pnpm add @english-town/scene-schema@workspace:*`。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@english-town/scene-schema': fileURLToPath(new URL('../../packages/scene-schema/src/index.ts', import.meta.url)),
    },
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], include: ['tests/**/*.{test,spec}.?(c|m)[jt]s?(x)'] },
});
