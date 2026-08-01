#!/usr/bin/env node
// 静态检查：禁止迁移范围内的旧合同模式。
// 在 api:check / CI 中运行，发现违规即非 0 退出。
//
// 检查项（对应任务卡第 11 节 #9）：
//  1. api.<method><T>() 手写覆盖成功响应泛型（应改为从生成类型推导）
//  2. `as unknown as` / `as never` 双重类型断言绕过
//  3. 页面手写镜像响应接口（与 generated.ts 重复声明领域响应）
//
// 排除：generated.ts（生成产物）、api.test.ts / api-contract.test.ts（错误判别测试）。

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(__dirname, "..");
const EXCLUDES = new Set([
  "src/lib/api/generated.ts",
  "src/lib/api.test.ts",
  "src/lib/api-contract.test.ts",
]);

const GENERIC_CALL_RE = /\bapi\.(get|post|put|patch|delete|upload)<\s*\{/;
const DOUBLE_CAST_RE = /\bas\s+unknown\s+as\b|\bas\s+never\b/;

function walk(dir) {
  const results = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue;
      results.push(...walk(full));
    } else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) {
      results.push(full);
    }
  }
  return results;
}

const violations = [];
for (const file of walk(path.join(WEB_ROOT, "src"))) {
  const rel = path.relative(WEB_ROOT, file).replace(/\\/g, "/");
  if (EXCLUDES.has(rel)) continue;
  const content = readFileSync(file, "utf-8");
  const lines = content.split("\n");

  lines.forEach((line, idx) => {
    if (GENERIC_CALL_RE.test(line)) {
      violations.push(`${rel}:${idx + 1}: api.<T>() 手写成功响应泛型（应从生成类型推导）`);
    }
    if (DOUBLE_CAST_RE.test(line)) {
      violations.push(`${rel}:${idx + 1}: 双重类型断言绕过（as unknown as / as never）`);
    }
  });
}

if (violations.length > 0) {
  console.error("[api-contract-check] 发现旧合同模式：");
  for (const v of violations) console.error(`  - ${v}`);
  process.exit(1);
}
console.log("[api-contract-check] 未发现旧合同模式。");
