import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  TokenStore,
  getToken,
  handleAuthFailure,
  login,
  logout,
  onAuthFailure,
  refreshToken,
  resetAuthFailureGuard,
  subscribeToTokenChange,
} from "@/lib/auth";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

describe("统一令牌模块", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
    resetAuthFailureGuard();
  });

  afterEach(() => {
    server.restore();
  });

  it("TokenStore 是令牌读写唯一入口：setTokens/getAccessToken 一致", () => {
    TokenStore.setTokens("access-1", "refresh-1");
    expect(TokenStore.getAccessToken()).toBe("access-1");
    expect(TokenStore.getRefreshToken()).toBe("refresh-1");
    expect(getToken()).toBe("access-1");
  });

  it("clearTokens 原子清除两类令牌并通知订阅方", () => {
    const listener = vi.fn();
    const unsub = subscribeToTokenChange(listener);
    TokenStore.setTokens("a", "r");
    expect(listener).toHaveBeenCalledTimes(1);

    TokenStore.clearTokens();
    expect(TokenStore.getAccessToken()).toBeNull();
    expect(TokenStore.getRefreshToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(2);

    unsub();
  });

  it("login 写入令牌并返回用户资料", async () => {
    server.onPost("/auth/login", {
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    server.onGet("/auth/me", { username: "alice", email: "a@t", role: "admin" });

    const data = await login("alice", "pw");
    expect(data.user.username).toBe("alice");
    expect(localStorage.getItem("access_token")).toBe("access-1");
  });

  it("logout 清除令牌", async () => {
    TokenStore.setTokens("a", "r");
    logout();
    expect(TokenStore.getAccessToken()).toBeNull();
    expect(TokenStore.getRefreshToken()).toBeNull();
  });

  it("refreshToken 无 refresh token 时返回 false", async () => {
    localStorage.clear();
    expect(await refreshToken()).toBe(false);
  });

  it("refreshToken 成功时写回新令牌", async () => {
    localStorage.setItem("refresh_token", "refresh-1");
    server.onPost("/auth/refresh", {
      access_token: "new-access",
      refresh_token: "new-refresh",
    });

    expect(await refreshToken()).toBe(true);
    expect(localStorage.getItem("access_token")).toBe("new-access");
    expect(localStorage.getItem("refresh_token")).toBe("new-refresh");
  });

  it("refreshToken 失败时不修改现有令牌", async () => {
    localStorage.setItem("access_token", "old-access");
    localStorage.setItem("refresh_token", "refresh-1");
    server.mock("POST", "/auth/refresh", { status: 401, body: { detail: "x" } });

    expect(await refreshToken()).toBe(false);
    expect(localStorage.getItem("access_token")).toBe("old-access");
  });

  it("refreshToken 响应格式错误（缺 refresh_token）时返回 false", async () => {
    localStorage.setItem("refresh_token", "refresh-1");
    server.onPost("/auth/refresh", { access_token: "only-access" });

    expect(await refreshToken()).toBe(false);
  });

  it("20 个并发 refreshToken 只发 1 次刷新请求", async () => {
    localStorage.setItem("refresh_token", "refresh-1");
    server.onPost("/auth/refresh", {
      access_token: "new-access",
      refresh_token: "new-refresh",
    });

    const results = await Promise.all(Array.from({ length: 20 }, () => refreshToken()));
    expect(results.every((r) => r === true)).toBe(true);
    expect(server.getHandler("POST", "/auth/refresh")?.callCount).toBe(1);
    expect(localStorage.getItem("access_token")).toBe("new-access");
  });

  it("旧刷新结果晚到时不得覆盖较新的登录会话（令牌代数）", async () => {
    vi.useFakeTimers();
    try {
      localStorage.setItem("refresh_token", "refresh-1");

      // 第一次刷新请求延迟 100ms（慢），期间发生新登录
      server.mock("POST", "/auth/refresh", {
        status: 200,
        body: { access_token: "slow-access", refresh_token: "slow-refresh" },
        delay: 100,
      });

      const slowRefresh = refreshToken(); // 发起慢刷新，捕获代数

      // 慢刷新在途期间：发生一次新登录（代数递增）
      await vi.advanceTimersByTimeAsync(10);
      TokenStore.setTokens("newer-access", "newer-refresh");

      // 放行慢刷新响应
      await vi.advanceTimersByTimeAsync(200);
      const slowResult = await slowRefresh;

      // 慢刷新的写入被丢弃，保留新会话
      expect(slowResult).toBe(true);
      expect(localStorage.getItem("access_token")).toBe("newer-access");
      expect(localStorage.getItem("refresh_token")).toBe("newer-refresh");
    } finally {
      vi.useRealTimers();
    }
  });

  it("handleAuthFailure 原子清除令牌并只触发一次回调", () => {
    const listener = vi.fn();
    const unsub = onAuthFailure(listener);

    TokenStore.setTokens("a", "r");
    handleAuthFailure();
    handleAuthFailure(); // 第二次不应重复触发

    expect(TokenStore.getAccessToken()).toBeNull();
    expect(TokenStore.getRefreshToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);

    unsub();
  });
});
