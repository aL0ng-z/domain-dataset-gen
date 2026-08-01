import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { api, ApiErrorException, formatJsonPreview } from "@/lib/api";

/**
 * 前端合同 mock 测试（api-contract）。
 *
 * 用真实 ASGI 合同形状喂给前端 mock，验证列表/详情页可渲染，
 * 不出现 `.slice is not a function`、React object child 或 `data.length` 异常。
 *
 * 覆盖任务卡第 11 节：
 * - 分页四键响应渲染（空集/单页）
 * - Candidate/CuratedItem content 为 object（formatJsonPreview 安全渲染）
 * - 错误 envelope 判别（business/validation）
 * - 旧请求字段被 extra=forbid 拒绝（返回 422，前端解析为 validation）
 */

// 使用一个轻量的渲染占位：直接验证 api 客户端 + formatJsonPreview 的行为，
// 页面级渲染由 login 测试模式覆盖（此处聚焦合同形状）。
describe("api-contract: 分页与 JSON 字段合同", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
  });

  afterEach(() => {
    server.restore();
  });

  it("列表页读取规范四键分页响应（空集也能渲染）", async () => {
    // mock 把 query 并入匹配路径。
    server.onGet("/projects/p1/datasets/d1/items?page=1&page_size=20", {
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });

    const data = await api.get("/projects/{pid}/datasets/{did}/items", {
      params: { pid: "p1", did: "d1" },
      query: { page: 1, page_size: 20 },
    });
    // 四键结构：页面不再对裸数组访问 .items/.total
    expect(data).toHaveProperty("items");
    expect(data).toHaveProperty("total");
    expect(data).toHaveProperty("page");
    expect(data).toHaveProperty("page_size");
    expect(Array.isArray(data.items)).toBe(true);
  });

  it("Candidate content 为 object 时 formatJsonPreview 安全渲染", () => {
    const content = { title: "压缩机", tags: ["核心知识"] };
    const preview = formatJsonPreview(content);
    expect(preview).toContain('"title"');
    expect(preview).not.toContain("[object Object]");
  });

  it("CuratedItem content 为 object，页面不触发 React object child 异常", () => {
    const content = { summary: "……" };
    // formatJsonPreview 是页面渲染 JSON 的共享入口
    expect(formatJsonPreview(content)).toBe(JSON.stringify(content, null, 2));
  });

  it("旧请求字段（extra 字段）返回 422，前端解析为 validation 错误", async () => {
    server.mock("PATCH", "/candidates/c1", {
      status: 422,
      body: {
        code: "VALIDATION_ERROR",
        message: "请求参数校验失败",
        errors: [
          {
            loc: ["body", "template_id"],
            msg: "Extra inputs are not permitted",
            type: "extra_forbidden",
          },
        ],
      },
    });

    const err: ApiErrorException = await api
      .patch("/candidates/{cid}", { template_id: "old-field" } as never, {
        params: { cid: "c1" },
      })
      .then(
        () => new Error("unexpected resolve") as never,
        (e: unknown) => e as ApiErrorException,
      );
    expect(err.apiError.kind).toBe("validation");
    expect(err.apiError.code).toBe("VALIDATION_ERROR");
    if (err.apiError.kind === "validation") {
      expect(err.apiError.errors[0].loc).toContain("template_id");
    }
  });

  it("业务错误按稳定 code 判别，不读取中文 detail", async () => {
    server.mock("GET", "/projects/p1/datasets/", {
      status: 404,
      body: {
        code: "NOT_FOUND",
        message: "数据集不存在",
        request_id: "req-xyz",
      },
    });

    const err: ApiErrorException = await api
      .get("/projects/{pid}/datasets/", { params: { pid: "p1" } })
      .then(
        () => new Error("unexpected resolve") as never,
        (e: unknown) => e as ApiErrorException,
      );
    expect(err.apiError.code).toBe("NOT_FOUND");
    expect(err.apiError.requestId).toBe("req-xyz");
    // 前端按 code 分支，不匹配 message
    expect(err.apiError.kind).toBe("business");
  });

  it("204 无 body 的删除返回 void（不抛 React 渲染异常）", async () => {
    server.mock("DELETE", "/projects/p1/datasets/d1/items/i1", { status: 204 });

    const result = await api.delete("/projects/{pid}/datasets/{did}/items/{item_id}", {
      params: { pid: "p1", did: "d1", item_id: "i1" },
    });
    expect(result).toBeUndefined();
  });
});
