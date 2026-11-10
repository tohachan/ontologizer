/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The bundle is served by the Python service from the same origin as the API (docs/visualizer.md).
// `npm run dev` proxies the API to a service started with `ontologizer visualizer`.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', sourcemap: false, emptyOutDir: true },
  server: { proxy: { '/visualizer/api': { target: 'http://127.0.0.1:8090', changeOrigin: true } } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: false },
});
