/**
 * T02 前端 PDF 授权：api.getBlob 携带 Authorization、无 query token、
 * 失败不回退、401 刷新重试。
 *
 * 覆盖任务卡 §6：
 * - PDF 通过 apiFetch 携带 Authorization 下载 Blob；
 * - PDF 请求失败时不得回退为带 token 的 query URL；
 * - 401 走刷新重试。
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/lib/api";
import { resetAuthFailureGuard } from "@/lib/auth";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

describe("api.getBlob 授权下载", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
    resetAuthFailureGuard();
    localStorage.setItem("access_token", "token-abc");
  });

  afterEach(() => {
    server.restore();
    localStorage.clear();
  });

  it("携带 Authorization 头下载 PDF Blob，不出现 query token", async () => {
    server.mock("GET", "/projects/p1/documents/d1/file", {
      status: 200,
      body: new Blob(["%PDF-1.4"], { type: "application/pdf" }),
      headers: { "Content-Type": "application/pdf" },
    });

    const blob = await api.getBlob("/projects/p1/documents/d1/file");
    // jsdom 中 res.blob() 返回 undici realm 的 Blob，跨 realm 不能用 toBeInstanceOf。
    expect(blob.size).toBe(8);
    expect(blob.type).toBe("application/pdf");

    const calls = server.getHandler("GET", "/projects/p1/documents/d1/file")?.calls ?? [];
    expect(calls.length).toBe(1);
    const auth = (calls[0].options?.headers as Record<string, string> | undefined)?.["Authorization"];
    expect(auth).toBe("Bearer token-abc");
    // URL 不含 token query 参数（Blob 下载不回退为 query URL）。
    expect(calls[0].url).not.toContain("token=");
  });

  it("401 触发刷新并用新令牌重试", async () => {
    localStorage.setItem("refresh_token", "refresh-1");
    server.mock("GET", "/projects/p1/documents/d1/file", ({ callCount }) =>
      callCount === 1
        ? { status: 401, body: { detail: "unauthorized" } }
        : {
            status: 200,
            body: new Blob(["%PDF-1.4"], { type: "application/pdf" }),
            headers: { "Content-Type": "application/pdf" },
          },
    );
    server.onPost("/auth/refresh", { access_token: "new-token", refresh_token: "refresh-2" });

    const blob = await api.getBlob("/projects/p1/documents/d1/file");
    // jsdom 中 res.blob() 返回 undici realm 的 Blob，跨 realm 不能用 toBeInstanceOf。
    expect(blob.size).toBe(8);
    expect(blob.type).toBe("application/pdf");
    expect(localStorage.getItem("access_token")).toBe("new-token");
    expect(server.getHandler("GET", "/projects/p1/documents/d1/file")?.callCount).toBe(2);
  });

  it("404 抛错且不生成任何带 token 的 URL", async () => {
    server.mock("GET", "/projects/p1/documents/d2/file", {
      status: 404,
      body: { detail: "资源不存在" },
    });

    await expect(api.getBlob("/projects/p1/documents/d2/file")).rejects.toThrow();
    const calls = server.getHandler("GET", "/projects/p1/documents/d2/file")?.calls ?? [];
    expect(calls[0].url).not.toContain("token=");
  });
});
