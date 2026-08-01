import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * 确定性前端合同生成命令：api:generate
 *
 * 流程：
 *   1. 尽量刷新 OpenAPI 快照（调用后端 python scripts/export_openapi.py）；
 *      若无法定位 Python/conda，则使用仓库已提交的 openapi.json（漂移由
 *      api:check 门禁兜底）。
 *   2. 用 openapi-typescript 生成 src/lib/api/generated.ts（paths/components/operations）。
 *
 * 生成产物是前端类型与类型化 client 的唯一事实源；文件头明确“禁止手工编辑”。
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../..");
const OPENAPI_JSON = path.join(REPO_ROOT, "apps/api/openapi.json");
const OUTPUT_TS = path.join(__dirname, "../src/lib/api/generated.ts");

function tryRefreshOpenApi() {
  const candidates = [
    // conda（Windows 本机）
    { cmd: "conda", args: ["run", "-n", "DatasetGen", "python", "scripts/export_openapi.py"], cwd: REPO_ROOT },
    // uv（CI）
    { cmd: "uv", args: ["run", "python", "scripts/export_openapi.py"], cwd: REPO_ROOT },
    // 系统 python
    { cmd: "python", args: ["scripts/export_openapi.py"], cwd: REPO_ROOT },
  ];
  for (const c of candidates) {
    try {
      execFileSync(c.cmd, c.args, { cwd: c.cwd, stdio: "pipe", encoding: "utf-8", timeout: 60_000 });
      return true;
    } catch {
      // 尝试下一种解释器
    }
  }
  return false;
}

function generate() {
  if (!existsSync(OPENAPI_JSON)) {
    throw new Error(`缺少 OpenAPI 快照：${OPENAPI_JSON}；请先在仓库根目录运行 python scripts/export_openapi.py`);
  }
  const refreshed = tryRefreshOpenApi();
  if (!refreshed) {
    console.warn("[api:generate] 无法调用后端导出脚本，使用仓库已提交的 openapi.json（漂移由 api:check 门禁兜底）。");
  }
  execFileSync(
    process.execPath,
    [path.join(__dirname, "../node_modules/openapi-typescript/bin/cli.js"), OPENAPI_JSON, "-o", OUTPUT_TS],
    { stdio: "inherit" },
  );
  console.log(`[api:generate] 已生成 ${path.relative(REPO_ROOT, OUTPUT_TS)}`);
}

generate();
