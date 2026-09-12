import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import CleaningWorkbenchPage from "./page";

const { router, query } = vi.hoisted(() => ({
  router: { push: vi.fn(), replace: vi.fn() },
  query: new URLSearchParams("cleaning_job_id=job-1"),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", did: "doc-1" }),
  useRouter: () => router,
  useSearchParams: () => query,
}));
vi.mock("@/hooks/use-ws", () => ({ useWs: () => ({ lastMessage: null }) }));
vi.mock("next/dynamic", () => ({
  default: () => function Editor({ value, onChange, readOnly }: {
    value: string; onChange: (text: string) => void; readOnly: boolean;
  }) {
    return <textarea aria-label="章节正文" value={value} onChange={(event) => onChange(event.target.value)} readOnly={readOnly} />;
  },
}));

const section = (id: string, content = `${id} 原文`) => ({
  id, document_id: "doc-1", cleaning_job_id: "job-1", ordinal: id === "a" ? 0 : 1,
  heading_path: `章节 ${id}`, cleaned_markdown: content, raw_markdown: content,
  content_revision: 1, status: "in_cleaning", assignment_status: "in_progress", assigned_to: "u1",
});

describe("清洗页面保存与继续", () => {
  let server: ReturnType<typeof createApiMockServer>;
  beforeEach(() => {
    server = createApiMockServer();
    server.install();
    server.onGet("/auth/me", { id: "u1", role: "reviewer" });
    server.onGet("/projects/p1/access", { effective_role: "reviewer" });
    server.onGet("/projects/p1/members", []);
    server.onGet("/projects/p1/documents/doc-1/cleaning-jobs", [{ id: "job-1", status: "completed", created_at: "2026-09-12T00:00:00Z" }]);
    server.onGet("/projects/p1/documents/doc-1/sections?page=1&page_size=100&cleaning_job_id=job-1", {
      items: [section("a"), section("b")], total: 2,
    });
    server.onGet("/projects/p1/documents/doc-1/cleaning/versions?cleaning_job_id=job-1", []);
    for (const id of ["a", "b"]) {
      server.onGet(`/sections/${id}`, section(id));
      server.onGet(`/sections/${id}/comments`, []);
      server.onPost(`/sections/${id}/lease/acquire`, { id: `lease-${id}`, section_id: id });
      server.onPost(`/sections/${id}/lease/release`, {});
    }
  });
  afterEach(() => server.restore());

  async function editAndSwitch() {
    render(<CleaningWorkbenchPage />);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "章节正文" })).not.toHaveAttribute("readonly"));
    const editor = screen.getByRole("textbox", { name: "章节正文" });
    fireEvent.change(editor, { target: { value: "我的最新草稿" } });
    fireEvent.click(screen.getByRole("button", { name: /2\. 章节 b/ }));
    expect(await screen.findByText("有未保存的修改")).toBeInTheDocument();
    return editor;
  }

  it.each([
    { status: 409, body: { code: "SECTION_VERSION_CONFLICT", message: "changed", context: { current_revision: 3 } } },
    { status: 409, body: { code: "SECTION_LEASE_LOST", message: "lost" } },
    { status: 500, body: {} },
    { networkError: true },
  ])("保存错误 $status 后仍停留原章节并逐字保留草稿和确认框", async (response) => {
    const patch = server.mock("PATCH", "/sections/a", response);
    const editor = await editAndSwitch();
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
    await waitFor(() => expect(patch.callCount).toBe(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存并继续", hidden: true })).not.toBeDisabled());
    expect(editor).toHaveValue("我的最新草稿");
    expect(screen.getByText("有未保存的修改")).toBeInTheDocument();
    expect(server.wasCalled("GET", "/sections/b")).toBe(false);
  });

  it("保存成功才加载下一章节，详情和 acquire 每次只发一次", async () => {
    const patch = server.mock("PATCH", "/sections/a", {
      body: { ...section("a", "我的最新草稿"), content_revision: 2 }, delay: 30,
    });
    await editAndSwitch();
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
    expect(server.wasCalled("GET", "/sections/b")).toBe(false);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "章节正文" })).toHaveValue("b 原文"));
    expect(patch.callCount).toBe(1);
    expect(server.getHandler("GET", "/sections/a")?.callCount).toBe(1);
    expect(server.getHandler("POST", "/sections/a/lease/acquire")?.callCount).toBe(1);
    expect(server.getHandler("GET", "/sections/b")?.callCount).toBe(1);
    expect(server.getHandler("POST", "/sections/b/lease/acquire")?.callCount).toBe(1);
  });

  it("返回文档也先保护草稿，保存失败不会导航", async () => {
    render(<CleaningWorkbenchPage />);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "章节正文" })).not.toHaveAttribute("readonly"));
    const editor = screen.getByRole("textbox", { name: "章节正文" });
    fireEvent.change(editor, { target: { value: "保留我" } });
    server.mock("PATCH", "/sections/a", { networkError: true });
    fireEvent.click(screen.getByRole("link", { name: "返回文档" }));
    fireEvent.click(await screen.findByRole("button", { name: "保存并继续" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存并继续" })).not.toBeDisabled());
    expect(router.push).not.toHaveBeenCalled();
    expect(editor).toHaveValue("保留我");
  });

  it("租约失效后可从页面重新获取，草稿不被重新加载覆盖", async () => {
    server.mock("PATCH", "/sections/a", { status: 409, body: { code: "SECTION_LEASE_LOST", message: "lost" } });
    await editAndSwitch();
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "留在当前页" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "留在当前页" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新获取租约" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "章节正文" })).not.toHaveAttribute("readonly"));
    expect(screen.getByRole("textbox", { name: "章节正文" })).toHaveValue("我的最新草稿");
    expect(server.getHandler("POST", "/sections/a/lease/acquire")?.callCount).toBe(2);
  });
});
