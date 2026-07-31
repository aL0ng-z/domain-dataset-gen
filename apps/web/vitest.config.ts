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
      reporter: ["text", "json-summary", "html"],
      reportsDirectory: "coverage",
    },
  },
});
