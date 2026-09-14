import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiErrorException, isAbortError } from "@/lib/api";
import { onAuthFailure, resetAuthFailureGuard, TokenStore } from "@/lib/auth";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

describe("api client with mock server", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
    resetAuthFailureGuard();
  });

  afterEach(() => {
    server.restore();
  });

  it("GET 按真实 JSON 响应解析（返回类型从 OpenAPI 推导）", async () => {
    server.onGet("/auth/me", { username: "alice", email: "a@t", role: "admin" });

    const data = await api.get("/auth/me");

    expect(data.username).toBe("alice");
    expect(server.wasCalled("GET", "/auth/me")).toBe(true);
  });

  it("GET 401 触发 refresh 并重试成功", async () => {
    TokenStore.setTokens("old-token", "refresh-1");

    // /auth/me 第一次返回 401，之后返回成功（401→refresh→重试的序列响应）。
    server.mock("GET", "/auth/me", ({ callCount }) =>
      callCount === 1
        ? { status: 401, body: { code: "AUTH_REQUIRED", message: "unauthorized" } }
        : { status: 200, body: { username: "alice", email: "a@t", role: "admin" } },
    );
    server.onPost("/auth/refresh", { access_token: "new-token", refresh_token: "refresh-2" });

    const data = await api.get("/auth/me");

    expect(data.username).toBe("alice");
    // 第一次调用 401，第二次成功 -> callCount 2
    expect(server.getHandler("GET", "/auth/me")?.callCount).toBe(2);
    expect(TokenStore.getAccessToken()).toBe("new-token");
  });

  it("非 2xx 抛 ApiErrorException，按稳定 code 分支而非中文 detail", async () => {
    server.mock("GET", "/projects/", {
      status: 500,
      body: { code: "INTERNAL_ERROR", message: "内部错误", request_id: "req-1" },
    });

    const err: ApiErrorException = await api.get("/projects/").then(
      () => new Error("unexpected resolve") as never,
      (e: unknown) => e as ApiErrorException,
    );
    expect(err).toBeInstanceOf(ApiErrorException);
    expect(err.apiError.code).toBe("INTERNAL_ERROR");
    expect(err.apiError.message).toBe("内部错误");
    expect(err.apiError.requestId).toBe("req-1");
  });

  it("422 解析为 validation 错误，保留字段位置", async () => {
    server.mock("GET", "/projects/", {
      status: 422,
      body: {
        code: "VALIDATION_ERROR",
        message: "请求参数校验失败",
        errors: [{ loc: ["query", "page"], msg: "Input should be greater than or equal to 1", type: "greater_than_equal" }],
      },
    });

    const err: ApiErrorException = await api.get("/projects/").then(
      () => new Error("unexpected resolve") as never,
      (e: unknown) => e as ApiErrorException,
    );
    expect(err.apiError.kind).toBe("validation");
    expect(err.apiError.code).toBe("VALIDATION_ERROR");
    if (err.apiError.kind === "validation") {
      expect(err.apiError.errors[0].loc).toContain("page");
    }
  });

  it("延迟响应 + fake timers 下等待完成", async () => {
    vi.useFakeTimers();
    try {
      server.onGet("/projects/", { items: [], total: 0, page: 1, page_size: 20 }, { delay: 500 });

      const promise = api.get("/projects/");
      // 未到延迟点不应完成
      const raced = await Promise.race([promise, Promise.resolve("pending")]);
      expect(raced).toBe("pending");

      await vi.advanceTimersByTimeAsync(500);
      const result = await promise;
      expect(result).toEqual({ items: [], total: 0, page: 1, page_size: 20 });
    } finally {
      vi.useRealTimers();
    }
  });

  it("AbortSignal 取消请求抛出 AbortError", async () => {
    const controller = new AbortController();

    server.mock("GET", "/projects/", { delay: 1000, body: { items: [], total: 0, page: 1, page_size: 20 } });

    const promise = api.get("/projects/", { signal: controller.signal });
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
    server.mock("GET", "/projects/", { networkError: true });

    await expect(api.get("/projects/")).rejects.toThrow("Failed to fetch");
  });

  it("GET 携带 query 参数", async () => {
    // mock 会把 query 并入匹配路径，因此注册带 query 的路由。
    server.onGet("/projects/?page=1&page_size=20", { items: [], total: 0, page: 1, page_size: 20 });

    await api.get("/projects/", { query: { page: 1, page_size: 20 } });

    const calls = server.getHandler("GET", "/projects/?page=1&page_size=20")?.calls ?? [];
    expect(calls[0].url).toContain("page=1");
    expect(calls[0].url).toContain("page_size=20");
  });

  it("20 个并发 401 只产生 1 次 refresh，成功后各自重试 1 次", async () => {
    TokenStore.setTokens("old-token", "refresh-1");

    // /items 每次都 401，之后 refresh 成功，重试也 401（用于统计重试次数）
    server.mock("GET", "/projects/", () => ({
      status: 401,
      body: { detail: "unauthorized" },
    }));
    server.onPost("/auth/refresh", {
      access_token: "new-token",
      refresh_token: "refresh-2",
    });

    const results = await Promise.all(
      Array.from({ length: 20 }, () =>
        api.get("/projects/").then(
          () => "resolved",
          () => "rejected",
        ),
      ),
    );

    // 刷新只发生 1 次（single-flight）
    expect(server.getHandler("POST", "/auth/refresh")?.callCount).toBe(1);
    // 每个请求只重试一次：初始 1 + 重试 1 = 2 次 /projects/ 调用，共 40 次
    expect(server.getHandler("GET", "/projects/")?.callCount).toBe(40);
    // 全部一致失败（重试后仍 401），无无限重试
    expect(results).toEqual(Array.from({ length: 20 }, () => "rejected"));
  });

  it("刷新失败时只执行一次清理，所有等待请求一致失败且不残留令牌", async () => {
    const failureListener = vi.fn();
    const unsub = onAuthFailure(failureListener);
    try {
      TokenStore.setTokens("old-token", "refresh-1");

      server.mock("GET", "/projects/", () => ({
        status: 401,
        body: { detail: "unauthorized" },
      }));
      server.mock("POST", "/auth/refresh", {
        status: 401,
        body: { detail: "refresh invalid" },
      });

      const results = await Promise.all(
        Array.from({ length: 20 }, () =>
          api.get("/projects/").then(
            () => "resolved",
            () => "rejected",
          ),
        ),
      );

      expect(server.getHandler("POST", "/auth/refresh")?.callCount).toBe(1);
      // 全部一致失败
      expect(results).toEqual(Array.from({ length: 20 }, () => "rejected"));
      // 只触发一次统一清理回调
      expect(failureListener).toHaveBeenCalledTimes(1);
      // 令牌已原子清除
      expect(TokenStore.getAccessToken()).toBeNull();
      expect(TokenStore.getRefreshToken()).toBeNull();
    } finally {
      unsub();
    }
  });

  it("login/refresh 请求本身不做 401 重试（排除刷新逻辑）", async () => {
    TokenStore.setTokens("old-token", "refresh-1");
    server.mock("POST", "/auth/refresh", {
      status: 401,
      body: { detail: "invalid" },
    });

    await expect(api.post("/auth/refresh", { refresh_token: "x" })).rejects.toThrow();
    // 只调用 1 次，不触发刷新循环
    expect(server.getHandler("POST", "/auth/refresh")?.callCount).toBe(1);
  });

  it("旧会话在等待刷新时切换账号，不会用新身份重发旧请求", async () => {
    vi.useFakeTimers();
    try {
      TokenStore.setTokens("old-access", "old-refresh");
      const update = server.mock("PATCH", "/candidates/c1", {
        status: 401,
        body: { code: "AUTH_REQUIRED", message: "expired" },
      });
      server.mock("POST", "/auth/refresh", {
        body: { access_token: "late", refresh_token: "late-refresh" },
        delay: 100,
      });

      const oldRequest = api.patch("/candidates/{cid}", {
        content: { question: "old" },
        expected_revision: 1,
      }, {
        params: { cid: "c1" },
      });
      const oldOutcome = oldRequest.then(
        () => new Error("unexpected resolve"),
        (error: unknown) => error,
      );
      await vi.advanceTimersByTimeAsync(1);
      TokenStore.startSession("new-access", "new-refresh");
      await vi.advanceTimersByTimeAsync(100);

      expect(await oldOutcome).toMatchObject({ name: "AbortError" });
      expect(update.callCount).toBe(1);
      expect((update.calls[0].options?.headers as Record<string, string>).Authorization).toBe("Bearer old-access");
      expect(TokenStore.getAccessToken()).toBe("new-access");
    } finally {
      vi.useRealTimers();
    }
  });

  it("截止时间覆盖已收到响应头后的 JSON body 读取", async () => {
    vi.useFakeTimers();
    const previousFetch = globalThis.fetch;
    try {
      globalThis.fetch = vi.fn(async () => ({
        ok: true,
        status: 200,
        json: () => new Promise((resolve) => setTimeout(() => resolve({ items: [] }), 100)),
      })) as unknown as typeof fetch;

      const pending = api.get("/projects/", { timeout: 10 });
      const outcome = pending.then(
        () => new Error("unexpected resolve"),
        (error: unknown) => error,
      );
      await vi.advanceTimersByTimeAsync(10);
      expect(await outcome).toMatchObject({ name: "TimeoutError" });
    } finally {
      globalThis.fetch = previousFetch;
      vi.useRealTimers();
    }
  });

  it("外部 AbortSignal 覆盖已收到响应头后的 JSON body 读取", async () => {
    const previousFetch = globalThis.fetch;
    try {
      globalThis.fetch = vi.fn(async () => ({
        ok: true,
        status: 200,
        json: () => new Promise((resolve) => setTimeout(() => resolve({ items: [] }), 100)),
      })) as unknown as typeof fetch;
      const controller = new AbortController();

      const pending = api.get("/projects/", { signal: controller.signal });
      const outcome = pending.then(
        () => new Error("unexpected resolve"),
        (error: unknown) => error,
      );
      await Promise.resolve();
      controller.abort();
      expect(await outcome).toMatchObject({ name: "AbortError" });
    } finally {
      globalThis.fetch = previousFetch;
    }
  });

  it("请求总截止时间也覆盖 401 后等待共享刷新", async () => {
    vi.useFakeTimers();
    try {
      TokenStore.setTokens("old-access", "refresh-1");
      server.mock("GET", "/projects/", {
        status: 401,
        body: { code: "AUTH_REQUIRED", message: "expired" },
      });
      server.mock("POST", "/auth/refresh", {
        body: { access_token: "late", refresh_token: "late-refresh" },
        delay: 100,
      });

      const pending = api.get("/projects/", { timeout: 10 });
      const outcome = pending.then(
        () => new Error("unexpected resolve"),
        (error: unknown) => error,
      );
      await vi.advanceTimersByTimeAsync(10);
      expect(await outcome).toMatchObject({ name: "TimeoutError" });
    } finally {
      vi.useRealTimers();
    }
  });
});
