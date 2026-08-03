import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // 单文件运行：npx vitest run src/lib/xxx.test.ts
    // CI 无交互：npm test -- --run
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary", "html"],
      reportsDirectory: "coverage",
      thresholds: {
        // 2026-08-03 首次完整基线减 2 个百分点：
        // statements 69.20 / branches 59.90 / functions 65.09 / lines 70.95。
        statements: 67.2,
        branches: 57.9,
        functions: 63.09,
        lines: 68.95,
      },
    },
  },
});
