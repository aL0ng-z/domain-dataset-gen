"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import ChunksPage from "./chunks/page";
import DocumentDetailPage from "./page";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", did: "d1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/status-badge", () => ({
  StatusBadge: ({ status }: { status: string }) => <span data-testid="badge">{status}</span>,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: React.ComponentProps<"button"> & { variant?: string; size?: string }) => (
    <button {...props}>{children}</button>
  ),
}));

vi.mock("@/components/data-table", () => ({
  DataTable: ({ data }: { data: unknown[] }) => (
    <div data-testid="data-table">{data.length} 行</div>
  ),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("ChunksPage 版本化切分", () => {
  it("默认请求 active set；可选历史 set 只读查看", async () => {
    server.onGet("/projects/p1/documents/d1/chunk-sets?page=1&page_size=50", {
      items: [
        { id: "set-2", version: 2, is_active: true, status: "completed", total_chunks: 3, total_tokens: 300, is_legacy: false, cleaned_document_version: 1 },
        { id: "set-1", version: 1, is_active: false, status: "completed", total_chunks: 2, total_tokens: 200, is_legacy: false },
      ],
      total: 2,
      page: 1,
      page_size: 50,
    });
    const chunksHandler = server.onGet("/projects/p1/documents/d1/chunks?page=1&page_size=20", {
      items: [
        { id: "c1", chunk_set_id: "set-2", chunk_set_version: 2, ordinal: 0, heading_path: "", content: "a", token_count: 10, status: "ready" },
        { id: "c2", chunk_set_id: "set-2", chunk_set_version: 2, ordinal: 1, heading_path: "", content: "b", token_count: 12, status: "ready" },
        { id: "c3", chunk_set_id: "set-2", chunk_set_version: 2, ordinal: 2, heading_path: "", content: "c", token_count: 11, status: "ready" },
      ],
      total: 3,
      page: 1,
      page_size: 50,
    });

    render(<ChunksPage />);
    await screen.findByText("分块列表");

    // 默认只请求 active set（不传 chunk_set_id query）。
    await waitFor(() => {
      expect(chunksHandler.callCount).toBeGreaterThan(0);
    });
    const activeCall = chunksHandler.calls[chunksHandler.calls.length - 1];
    expect(activeCall.url).not.toContain("chunk_set_id");

    // 页头显示 active set 版本与 hash。
    await screen.findByText(/Active 版本 v2/);
    await screen.findByText(/来源清洗版本/);
  });

  it("失败 set 显示失败原因并继续标识旧 active set", async () => {
    server.onGet("/projects/p1/documents/d1/chunk-sets?page=1&page_size=50", {
      items: [
        { id: "set-3", version: 3, is_active: false, status: "failed", error_message: "来源清洗版本已不是 active", total_chunks: 0, total_tokens: 0, is_legacy: false, cleaned_document_version: 2 },
        { id: "set-2", version: 2, is_active: true, status: "completed", total_chunks: 3, total_tokens: 300, is_legacy: false, cleaned_document_version: 1 },
      ],
      total: 2,
      page: 1,
      page_size: 50,
    });
    server.onGet("/projects/p1/documents/d1/chunks?page=1&page_size=20", {
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
    });

    render(<ChunksPage />);
    // 失败原因可见；active set 仍被标识。
    await screen.findByText(/失败原因：来源清洗版本已不是 active/);
    await screen.findByText(/旧 active 版本仍保留，可继续查看/);
    await screen.findByText(/Active 版本 v2/);
  });
});

describe("DocumentDetailPage 发起切分", () => {
  it("发送 Idempotency-Key 且网络重试不换 key", async () => {
    server.onGet("/projects/p1/documents/d1", { id: "d1", project_id: "p1", status: "cleaned", filename: "a.pdf", file_size: 10, sha256: "0".repeat(64), clean_status: "completed" });
    server.onGet("/projects/p1/documents/d1/parse-jobs", []);
    server.onGet("/projects/p1/documents/d1/cleaning-jobs", []);
    server.onGet("/projects/p1/parser-profiles/?page=1&page_size=50", { items: [], total: 0, page: 1, page_size: 50 });
    server.onGet("/projects/p1/chunk-profiles/?page=1&page_size=50", { items: [{ id: "prof-1", name: "默认切分", is_default: true, strategy: "hybrid_heading_recursive", max_tokens: 512, overlap_tokens: 50 }], total: 1, page: 1, page_size: 50 });
    server.onGet("/projects/p1/documents/d1/chunk-sets?page=1&page_size=50", { items: [], total: 0, page: 1, page_size: 20 });
    const chunkHandler = server.mock("POST", "/projects/p1/documents/d1/chunk", {
      status: 202,
      body: { task_id: "t1", chunk_set_id: "cs1", reused: false, status: "pending", message: "切分任务已创建" },
    });

    render(<DocumentDetailPage />);
    const button = await screen.findByRole("button", { name: /执行切分/ });
    await userEvent.click(button);

    await waitFor(() => expect(chunkHandler.callCount).toBe(1));
    const headers = chunkHandler.calls[0].options?.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBeTruthy();

    // 再次点击（模拟网络重试）：同 key 不换。
    await userEvent.click(button);
    await waitFor(() => expect(chunkHandler.callCount).toBe(2));
    const headers2 = chunkHandler.calls[1].options?.headers as Record<string, string>;
    expect(headers2["Idempotency-Key"]).toBe(headers["Idempotency-Key"]);
  });

  it("已有活跃切分时按钮禁用并显示切分中", async () => {
    server.onGet("/projects/p1/documents/d1", { id: "d1", project_id: "p1", status: "chunking", filename: "a.pdf", file_size: 10, sha256: "0".repeat(64), clean_status: "completed" });
    server.onGet("/projects/p1/documents/d1/parse-jobs", []);
    server.onGet("/projects/p1/documents/d1/cleaning-jobs", []);
    server.onGet("/projects/p1/parser-profiles/?page=1&page_size=50", { items: [], total: 0, page: 1, page_size: 50 });
    server.onGet("/projects/p1/chunk-profiles/?page=1&page_size=50", { items: [{ id: "prof-1", name: "默认切分", is_default: true, strategy: "hybrid_heading_recursive", max_tokens: 512, overlap_tokens: 50 }], total: 1, page: 1, page_size: 50 });
    server.onGet("/projects/p1/documents/d1/chunk-sets?page=1&page_size=20", { items: [{ id: "cs1", version: 1, is_active: false, status: "processing", total_chunks: 0, total_tokens: 0, is_legacy: false }], total: 1, page: 1, page_size: 20 });

    render(<DocumentDetailPage />);
    const button = await screen.findByRole("button", { name: /执行切分/ });
    await waitFor(() => expect(button).toBeDisabled());
    await screen.findByText(/(切分中)/);
  });
});
