import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, isAbortError } from "@/lib/api";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

describe("api client with mock server", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
  });

  afterEach(() => {
    server.restore();
  });

  it("GET 按真实 JSON 响应解析", async () => {
    server.onGet("/health", { status: "ok" });

    const data = await api.get<{ status: string }>("/health");

    expect(data.status).toBe("ok");
    expect(server.wasCalled("GET", "/health")).toBe(true);
  });

  it("GET 401 触发 refresh 并重试成功", async () => {
    localStorage.setItem("access_token", "old-token");
    localStorage.setItem("refresh_token", "refresh-1");

    // /me 第一次返回 401，之后返回成功（401→refresh→重试的序列响应）。
    server.mock("GET", "/me", ({ callCount }) =>
      callCount === 1
        ? { status: 401, body: { detail: "unauthorized" } }
        : { status: 200, body: { username: "alice" } },
    );
    server.onPost("/auth/refresh", { access_token: "new-token", refresh_token: "refresh-2" });

    const data = await api.get<{ username: string }>("/me");

    expect(data.username).toBe("alice");
    // 第一次调用 401，第二次成功 -> callCount 2
    expect(server.getHandler("GET", "/me")?.callCount).toBe(2);
    expect(localStorage.getItem("access_token")).toBe("new-token");
  });

  it("GET 500 抛错并包含后端 detail", async () => {
    server.mock("GET", "/projects", {
      status: 500,
      body: { detail: "内部错误" },
    });

    await expect(api.get("/projects")).rejects.toThrow("内部错误");
  });

  it("延迟响应 + fake timers 下等待完成", async () => {
    vi.useFakeTimers();
    try {
      server.onGet("/slow", { ok: true }, { delay: 500 });

      const promise = api.get("/slow");
      // 未到延迟点不应完成
      const raced = await Promise.race([promise, Promise.resolve("pending")]);
      expect(raced).toBe("pending");

      await vi.advanceTimersByTimeAsync(500);
      const result = await promise;
      expect(result).toEqual({ ok: true });
    } finally {
      vi.useRealTimers();
    }
  });

  it("AbortSignal 取消请求抛出 AbortError", async () => {
    const controller = new AbortController();

    server.mock("GET", "/slow", { delay: 1000, body: { ok: true } });

    const promise = api.get<{ ok: boolean }>("/slow", { signal: controller.signal });
    controller.abort();

    const err: Error = await promise.then(
      () => new Error("unexpected resolve"),
      (e: unknown) => e as Error,
    );
    // 请求中止抛出的错误语义是 AbortError（jsdom 中 DOMException 跨 realm
    // 不满足 instanceof Error，故只断言 name 这一真实语义）。
    expect(err.name).toBe("AbortError");
    // 非 AbortError 不应被识别
    expect(isAbortError(new Error("AbortError"))).toBe(false);
  });

  it("网络错误（Failed to fetch）向上传播", async () => {
    server.mock("GET", "/broken", { networkError: true });

    await expect(api.get("/broken")).rejects.toThrow("Failed to fetch");
  });
});
