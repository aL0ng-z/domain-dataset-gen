import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
/**
 * 前端合同漂移门禁：api:check
 *
 * 使用仓库已提交的 openapi.json（不是重新导出）在临时目录生成 generated.ts，
 * 与已提交的 src/lib/api/generated.ts 字节比较。任何一端漂移都会非 0 退出。
 * 这样开发者漏跑 api:generate 或后端改合同未同步时，CI 可靠失败。
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../..");
const OPENAPI_JSON = path.join(REPO_ROOT, "apps/api/openapi.json");
const COMMITTED_TS = path.join(__dirname, "../src/lib/api/generated.ts");
const CLI = path.join(__dirname, "../node_modules/openapi-typescript/bin/cli.js");

if (!existsSync(OPENAPI_JSON) || !existsSync(COMMITTED_TS)) {
  console.error("[api:check] 缺少 openapi.json 或 generated.ts；请先运行 npm run api:generate");
  process.exit(1);
}

const tmpDir = mkdtempSync(path.join(os.tmpdir(), "api-check-"));
const tmpOut = path.join(tmpDir, "generated.ts");
let regenerated;
try {
  execFileSync(process.execPath, [CLI, OPENAPI_JSON, "-o", tmpOut], { stdio: "pipe" });
  regenerated = readFileSync(tmpOut, "utf-8");
} finally {
  rmSync(tmpDir, { recursive: true, force: true });
}

const committed = readFileSync(COMMITTED_TS, "utf-8");

if (committed !== regenerated) {
  console.error("[api:check] 前端合同漂移：generated.ts 与 openapi.json 不一致。请运行 npm run api:generate 并提交最新产物。");
  process.exit(1);
}
console.log("[api:check] generated.ts 与 openapi.json 一致。");

// 静态检查：禁止旧合同模式（手写泛型覆盖、双重类型断言）。
try {
  execFileSync(process.execPath, [path.join(__dirname, "check-contract-patterns.mjs")], {
    stdio: "inherit",
  });
} catch {
  console.error("[api:check] 旧合同模式检查失败。");
  process.exit(1);
}
