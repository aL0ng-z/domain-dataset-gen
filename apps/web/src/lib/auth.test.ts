import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_SESSION_STORAGE_KEY,
  REFRESH_TIMEOUT,
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

  it("以单条记录保存 session_id 与两类令牌", () => {
    TokenStore.setTokens("access-1", "refresh-1");

    expect(TokenStore.getAccessToken()).toBe("access-1");
    expect(TokenStore.getRefreshToken()).toBe("refresh-1");
    expect(getToken()).toBe("access-1");
    const stored = JSON.parse(localStorage.getItem(AUTH_SESSION_STORAGE_KEY) ?? "{}") as Record<string, string>;
    expect(stored).toMatchObject({ access_token: "access-1", refresh_token: "refresh-1" });
    expect(stored.session_id).toEqual(expect.any(String));
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
  });

  it("clearTokens 原子清除整条会话并通知订阅方", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToTokenChange(listener);
    TokenStore.setTokens("a", "r");
    const sessionId = TokenStore.getSessionId();

    TokenStore.clearTokens();
    expect(TokenStore.getAccessToken()).toBeNull();
    expect(TokenStore.getRefreshToken()).toBeNull();
    expect(TokenStore.getSessionId()).toBeNull();
    expect(sessionId).toBeTruthy();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
  });

  it("login 写入一个新会话并返回用户资料", async () => {
    TokenStore.setTokens("old-access", "old-refresh");
    const oldSessionId = TokenStore.getSessionId();
    server.onPost("/auth/login", {
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    server.onGet("/auth/me", { username: "alice", email: "a@t", role: "admin" });

    const data = await login("alice", "pw");
    expect(data.user.username).toBe("alice");
    expect(TokenStore.getAccessToken()).toBe("access-1");
    expect(TokenStore.getSessionId()).not.toBe(oldSessionId);
  });

  it("logout 清除令牌", () => {
    TokenStore.setTokens("a", "r");
    logout();
    expect(TokenStore.getAccessToken()).toBeNull();
    expect(TokenStore.getRefreshToken()).toBeNull();
  });

  it("无会话时刷新被视为已取消", async () => {
    expect(await refreshToken()).toBe("cancelled");
  });

  it("刷新保留 session_id 并写回新令牌", async () => {
    TokenStore.setTokens("old-access", "refresh-1");
    const sessionId = TokenStore.getSessionId();
    server.onPost("/auth/refresh", {
      access_token: "new-access",
      refresh_token: "new-refresh",
    });

    expect(await refreshToken()).toBe("refreshed");
    expect(TokenStore.getAccessToken()).toBe("new-access");
    expect(TokenStore.getRefreshToken()).toBe("new-refresh");
    expect(TokenStore.getSessionId()).toBe(sessionId);
  });

  it("20 个并发刷新只发送一次请求", async () => {
    TokenStore.setTokens("old-access", "refresh-1");
    server.onPost("/auth/refresh", {
      access_token: "new-access",
      refresh_token: "new-refresh",
    });

    const results = await Promise.all(Array.from({ length: 20 }, () => refreshToken()));
    expect(results).toEqual(Array.from({ length: 20 }, () => "refreshed"));
    expect(server.getHandler("POST", "/auth/refresh")?.callCount).toBe(1);
  });

  it("单个调用取消只退出自己的刷新等待，不中止共享刷新", async () => {
    TokenStore.setTokens("old-access", "refresh-1");
    server.mock("POST", "/auth/refresh", {
      body: { access_token: "new-access", refresh_token: "new-refresh" },
      delay: 20,
    });
    const controller = new AbortController();

    const cancelled = refreshToken({ signal: controller.signal });
    const shared = refreshToken();
    controller.abort();

    expect(await cancelled).toBe("cancelled");
    expect(await shared).toBe("refreshed");
    expect(TokenStore.getAccessToken()).toBe("new-access");
  });

  it("会话切换取消旧刷新，旧响应不能覆盖新用户", async () => {
    vi.useFakeTimers();
    try {
      TokenStore.setTokens("old-access", "old-refresh");
      server.mock("POST", "/auth/refresh", {
        body: { access_token: "slow-access", refresh_token: "slow-refresh" },
        delay: 100,
      });

      const oldRefresh = refreshToken();
      await vi.advanceTimersByTimeAsync(10);
      TokenStore.startSession("new-access", "new-refresh");
      await vi.advanceTimersByTimeAsync(100);

      expect(await oldRefresh).toBe("cancelled");
      expect(TokenStore.getAccessToken()).toBe("new-access");
      expect(TokenStore.getRefreshToken()).toBe("new-refresh");
    } finally {
      vi.useRealTimers();
    }
  });

  it("刷新超时返回 failed，不无限等待", async () => {
    vi.useFakeTimers();
    try {
      TokenStore.setTokens("old-access", "refresh-1");
      server.mock("POST", "/auth/refresh", {
        body: { access_token: "late", refresh_token: "late-refresh" },
        delay: REFRESH_TIMEOUT + 1,
      });
      const pending = refreshToken();
      await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT + 1);
      expect(await pending).toBe("failed");
    } finally {
      vi.useRealTimers();
    }
  });

  it("旧会话认证失败不能清除新会话", () => {
    TokenStore.setTokens("old-access", "old-refresh");
    const oldSessionId = TokenStore.getSessionId();
    TokenStore.startSession("new-access", "new-refresh");
    const listener = vi.fn();
    const unsubscribe = onAuthFailure(listener);

    expect(handleAuthFailure(oldSessionId)).toBe(false);
    expect(TokenStore.getAccessToken()).toBe("new-access");
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("当前会话认证失败只清理一次", () => {
    const listener = vi.fn();
    const unsubscribe = onAuthFailure(listener);
    TokenStore.setTokens("a", "r");
    const sessionId = TokenStore.getSessionId();

    expect(handleAuthFailure(sessionId)).toBe(true);
    expect(handleAuthFailure(sessionId)).toBe(false);
    expect(TokenStore.getAccessToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
