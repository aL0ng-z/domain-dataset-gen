import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AuthProvider } from "@/contexts/auth-context";
import { useAuth } from "@/hooks/use-auth";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

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
    localStorage.setItem("access_token", "access-1");
    server.onGet("/auth/me", { username: "alice", email: "a@t", role: "admin" });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("username").textContent).toBe("alice"));
    expect(screen.getByTestId("status").textContent).toBe("authenticated");
  });

  it("/auth/me 失败时清理令牌并进入 anonymous", async () => {
    localStorage.setItem("access_token", "expired-token");
    server.mock("GET", "/auth/me", { status: 401, body: { detail: "x" } });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("anonymous"));
    expect(localStorage.getItem("access_token")).toBeNull();
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
    expect(localStorage.getItem("access_token")).toBeNull();
  });

  it("bootstrapping 阶段 loading 为 true，避免误跳登录页", async () => {
    localStorage.setItem("access_token", "access-1");
    server.mock("GET", "/auth/me", { delay: 100, body: { username: "alice", email: "a@t", role: "admin" } });

    renderProbe();
    // 初始为 bootstrapping + loading
    expect(screen.getByTestId("status").textContent).toBe("bootstrapping");
    expect(screen.getByTestId("loading").textContent).toBe("true");

    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
  });
});
