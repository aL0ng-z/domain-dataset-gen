import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";
import { AuthProvider } from "@/contexts/auth-context";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

// next/navigation 的 useRouter 在测试环境中无上下文，mock 掉。
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

function renderLogin() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>,
  );
}

describe("登录页", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
  });

  afterEach(() => {
    server.restore();
    mockPush.mockReset();
    window.history.replaceState(null, "", "/login");
  });

  it("成功登录后跳转到项目页", async () => {
    const user = userEvent.setup();
    server.onPost("/auth/login", {
      access_token: "access-1",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    server.onGet("/auth/me", { username: "admin", email: "a@t", role: "admin" });

    renderLogin();

    await user.type(screen.getByPlaceholderText("请输入用户名"), "admin");
    await user.type(screen.getByPlaceholderText("请输入密码"), "admin123");
    await user.click(screen.getByRole("button", { name: /登录/ }));

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/projects"));
    expect(localStorage.getItem("access_token")).toBe("access-1");
  });

  it("登录失败展示后端错误信息", async () => {
    const user = userEvent.setup();
    // 注意：onPost 只适合 200 响应；错误/延迟场景必须用 mock 显式指定 status。
    server.mock("POST", "/auth/login", {
      status: 401,
      body: { detail: "用户名或密码错误" },
    });

    renderLogin();

    await user.type(screen.getByPlaceholderText("请输入用户名"), "admin");
    await user.type(screen.getByPlaceholderText("请输入密码"), "wrong");
    await user.click(screen.getByRole("button", { name: /登录/ }));

    // 错误信息渲染到页面（真实 HTTP 401 + JSON detail）
    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it.each([
    ["/projects/p1/documents/d1?tab=clean", "/projects/p1/documents/d1?tab=clean"],
    ["//example.com", "/projects"],
    ["/\\example.com", "/projects"],
    ["/\n/example.com", "/projects"],
  ])("登录仅允许安全的站内回跳 %s", async (next, expected) => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", `/login?next=${encodeURIComponent(next)}`);
    server.onPost("/auth/login", { access_token: "a", refresh_token: "r" });
    server.onGet("/auth/me", { id: "u", username: "alice", role: "viewer" });
    renderLogin();
    await user.type(screen.getByPlaceholderText("请输入用户名"), "alice");
    await user.type(screen.getByPlaceholderText("请输入密码"), "pw");
    await user.click(screen.getByRole("button", { name: /登录/ }));
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith(expected));
  });

  it("登录期间按钮进入 loading 状态", async () => {
    const user = userEvent.setup();
    server.mock("POST", "/auth/login", {
      delay: 200,
      body: { access_token: "a", refresh_token: "r", token_type: "bearer" },
    });
    server.onGet("/auth/me", { username: "admin", email: "a@t", role: "admin" });

    renderLogin();

    await user.type(screen.getByPlaceholderText("请输入用户名"), "admin");
    await user.type(screen.getByPlaceholderText("请输入密码"), "admin123");
    await user.click(screen.getByRole("button", { name: /登录/ }));

    // 延迟期间按钮禁用
    const button = await screen.findByRole("button", { name: /登录/ });
    await waitFor(() => expect(button).toBeDisabled());

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/projects"));
  });
});
