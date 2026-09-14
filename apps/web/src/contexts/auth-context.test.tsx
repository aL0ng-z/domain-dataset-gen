import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AuthProvider } from "@/contexts/auth-context";
import { useAuth } from "@/hooks/use-auth";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { AUTH_SESSION_STORAGE_KEY, TokenStore } from "@/lib/auth";

function AuthProbe() {
  const { user, token, status, loading, logout, login } = useAuth();
  return (
    <div>
      <div data-testid="status">{status}</div>
      <div data-testid="loading">{String(loading)}</div>
      <div data-testid="token">{token ?? "null"}</div>
      <div data-testid="username">{user?.username ?? "null"}</div>
      <button onClick={() => login("alice", "pw")}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

function renderProbe() {
  return render(
    <AuthProvider>
      <AuthProbe />
    </AuthProvider>,
  );
}

describe("AuthProvider 认证状态机", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
  });

  afterEach(() => {
    server.restore();
  });

  it("无 token 时初始化为 anonymous，不进入 authenticated", async () => {
    localStorage.clear();
    renderProbe();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("anonymous"));
  });

  it("有 token 时拉取 /auth/me 并进入 authenticated", async () => {
    TokenStore.setTokens("access-1", "refresh-1");
    server.onGet("/auth/me", { username: "alice", email: "a@t", role: "admin" });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("username").textContent).toBe("alice"));
    expect(screen.getByTestId("status").textContent).toBe("authenticated");
  });

  it("/auth/me 失败时清理令牌并进入 anonymous", async () => {
    TokenStore.setTokens("expired-token", "refresh-1");
    server.mock("GET", "/auth/me", { status: 401, body: { detail: "x" } });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("anonymous"));
    expect(TokenStore.getAccessToken()).toBeNull();
  });

  it("login 后进入 authenticated，logout 后进入 anonymous", async () => {
    server.onPost("/auth/login", {
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    server.onGet("/auth/me", { username: "alice", email: "a@t", role: "admin" });

    renderProbe();
    await screen.findByText("login").then((b) => b.click());

    await waitFor(() => expect(screen.getByTestId("username").textContent).toBe("alice"));
    expect(screen.getByTestId("status").textContent).toBe("authenticated");

    await screen.findByText("logout").then((b) => b.click());
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("anonymous"));
    expect(TokenStore.getAccessToken()).toBeNull();
  });

  it("bootstrapping 阶段 loading 为 true，避免误跳登录页", async () => {
    TokenStore.setTokens("access-1", "refresh-1");
    server.mock("GET", "/auth/me", { delay: 100, body: { username: "alice", email: "a@t", role: "admin" } });

    renderProbe();
    // 初始为 bootstrapping + loading
    expect(screen.getByTestId("status").textContent).toBe("bootstrapping");
    expect(screen.getByTestId("loading").textContent).toBe("true");

    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
    expect(screen.getByTestId("loading").textContent).toBe("true");
    await waitFor(() => expect(screen.getByTestId("username").textContent).toBe("alice"));
    expect(screen.getByTestId("loading").textContent).toBe("false");
    expect(screen.getByTestId("token").textContent).toBe("access-1");
  });

  it("退出后晚到的 me 响应不能恢复用户", async () => {
    TokenStore.setTokens("old", "refresh-1");
    server.onGet("/auth/me", { username: "old-user", role: "admin" }, { delay: 80 });
    renderProbe();
    fireEvent.click(screen.getByText("logout"));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); });
    expect(screen.getByTestId("username").textContent).toBe("null");
    expect(screen.getByTestId("status").textContent).toBe("anonymous");
  });

  it("登录请求期间退出，晚到的登录结果不能写回令牌", async () => {
    server.onPost("/auth/login", { access_token: "late", refresh_token: "late-refresh" }, { delay: 50 });
    server.onGet("/auth/me", { username: "late-user", role: "viewer" });
    // AuthProbe 的按钮调用主动捕获取消，避免把预期 AbortError 当作未处理异常。
    function Probe() {
      const auth = useAuth();
      return <><div data-testid="state">{auth.status}</div>
        <button onClick={() => { void auth.login("alice", "pw").catch(() => {}); }}>start</button>
        <button onClick={auth.logout}>stop</button></>;
    }
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("anonymous"));
    fireEvent.click(screen.getByText("start"));
    fireEvent.click(screen.getByText("stop"));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)); });
    expect(TokenStore.getAccessToken()).toBeNull();
    expect(screen.getByTestId("state").textContent).toBe("anonymous");
  });

  it("跨标签页切换账号必须重新获取用户，旧响应不能覆盖", async () => {
    TokenStore.setTokens("old", "refresh-1");
    server.mock("GET", "/auth/me", ({ init }) => ({
      delay: (init?.headers as Record<string, string>)?.Authorization === "Bearer old" ? 100 : 5,
      body: { username: (init?.headers as Record<string, string>)?.Authorization === "Bearer old" ? "old-user" : "new-user", role: "viewer" },
    }));
    renderProbe();
    await act(async () => {
      const oldValue = localStorage.getItem(AUTH_SESSION_STORAGE_KEY);
      const newValue = JSON.stringify({
        session_id: "other-session",
        access_token: "new",
        refresh_token: "new-refresh",
      });
      localStorage.setItem(AUTH_SESSION_STORAGE_KEY, newValue);
      window.dispatchEvent(new StorageEvent("storage", {
        key: AUTH_SESSION_STORAGE_KEY,
        oldValue,
        newValue,
      }));
    });
    await waitFor(() => expect(screen.getByTestId("username").textContent).toBe("new-user"));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); });
    expect(screen.getByTestId("username").textContent).toBe("new-user");
    expect(screen.getByTestId("token").textContent).toBe("new");
  });
});
