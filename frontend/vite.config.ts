import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    // 测试写入下载文件时，Windows可能暂时锁定文件；这些产物无需触发热更新。
    watch: {
      ignored: [
        "**/artifacts/**",
        "**/test-results/**",
        "**/playwright-report/**",
      ],
    },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // 审核模式的前端等待上限为10分钟，代理不能在5分钟时先切断连接。
        timeout: 600_000,
      },
    },
  },
});
