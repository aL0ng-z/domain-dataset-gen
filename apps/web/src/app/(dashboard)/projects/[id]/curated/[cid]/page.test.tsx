"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { toast } from "sonner";
import CuratedItemDetailPage from "./page";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", cid: "item-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/status-badge", () => ({
  StatusBadge: ({ status }: { status: string }) => (
    <span data-testid="badge">{status}</span>
  ),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    ...props
  }: React.ComponentProps<"button"> & { variant?: string; size?: string }) => (
    <button {...props}>{children}</button>
  ),
}));

vi.mock("@/components/ui/card", () => ({
  Card: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="card">{children}</div>
  ),
  CardContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CardHeader: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CardTitle: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CardDescription: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const draftItem = {
  id: "item-1",
  project_id: "p1",
  candidate_id: "cand-1",
  content: { question: "压比定义", answer: "压比是出口与进口压力之比" },
  item_type: "qa_generation",
  status: "draft",
  promoted_by: "u1",
  current_revision: 2,
  approved_revision_id: null,
  approval_record_id: null,
  approved_by: null,
  approved_at: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const approvedItem = {
  ...draftItem,
  status: "approved",
  approved_revision_id: "rev-2",
  approval_record_id: "rec-2",
  approved_by: "u2",
  approved_at: "2026-08-02T00:00:00Z",
};

const evidence = {
  items: [
    {
      id: "el-1",
      curated_item_id: "item-1",
      document_id: "doc-1",
      chunk_id: "chunk-1",
      start_char: 0,
      end_char: 8,
      source_pages: { start: 1 },
      heading_path: "1.1",
      quote_text: "压比是出口",
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
};

const revisions = {
  items: [
    {
      id: "rev-2",
      curated_item_id: "item-1",
      revised_by: "u1",
      version: 2,
      content: { question: "压比定义", answer: "压比是出口与进口压力之比" },
      content_sha256: "a".repeat(64),
      canonicalization_version: "curated-content-cjson-v1",
      revision_note: "修正文案",
      created_at: "2026-08-02T00:00:00Z",
    },
    {
      id: "rev-1",
      curated_item_id: "item-1",
      revised_by: "u1",
      version: 1,
      content: { question: "压比定义", answer: "压比" },
      content_sha256: "b".repeat(64),
      canonicalization_version: "curated-content-cjson-v1",
      revision_note: "提升自 Candidate",
      created_at: "2026-08-01T00:00:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
};

function onMe() {
  server.onGet("/auth/me", { id: "u2", username: "reviewer", email: "r@x", role: "reviewer" });
}

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("CuratedItem 详情页", () => {
  it("分别加载 item/evidence/revisions 并渲染精确证据 offset 与 quote", async () => {
    onMe();
    server.onGet("/projects/p1/curated-items/item-1", draftItem);
    server.onGet(
      "/projects/p1/curated-items/item-1/evidence?page=1&page_size=20",
      evidence,
    );
    server.onGet(
      "/projects/p1/curated-items/item-1/revisions?page=1&page_size=20",
      revisions,
    );

    render(<CuratedItemDetailPage />);

    await waitFor(() => {
      expect(screen.getAllByText(/压比是出口/).length).toBeGreaterThan(0);
    });
    expect(screen.getByText(/\[0,8\)/)).toBeTruthy();
    expect(screen.getAllByText(/v2/).length).toBeGreaterThan(0);
  });

  it("reviewer 可见批准按钮；PATCH 携带 expected_revision；409 冲突保留草稿并显示服务端版本", async () => {
    onMe();
    server.onGet("/projects/p1/curated-items/item-1", draftItem);
    server.onGet(
      "/projects/p1/curated-items/item-1/evidence?page=1&page_size=20",
      evidence,
    );
    server.onGet(
      "/projects/p1/curated-items/item-1/revisions?page=1&page_size=20",
      revisions,
    );
    server.mock("PATCH", "/projects/p1/curated-items/item-1", {
      status: 409,
      body: {
        code: "CURATED_REVISION_CONFLICT",
        message: "expected_revision 不等于当前版本",
        context: { current_revision: 3 },
      },
    });

    render(<CuratedItemDetailPage />);

    await waitFor(() => {
      expect(screen.getAllByText(/压比是出口/).length).toBeGreaterThan(0);
    });

    // 保存（draft 可编辑）
    await userEvent.click(screen.getByText("保存"));
    expect(server.wasCalled("PATCH", "/projects/p1/curated-items/item-1")).toBe(
      true,
    );
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("版本冲突"),
      );
    });
    expect(screen.getByText(/服务端已更新到 v3/)).toBeTruthy();
  });

  it("approved 条目编辑框只读，reviewer 显示退审按钮", async () => {
    onMe();
    server.onGet("/projects/p1/curated-items/item-1", approvedItem);
    server.onGet(
      "/projects/p1/curated-items/item-1/evidence?page=1&page_size=20",
      evidence,
    );
    server.onGet(
      "/projects/p1/curated-items/item-1/revisions?page=1&page_size=20",
      revisions,
    );

    render(<CuratedItemDetailPage />);

    await waitFor(() => {
      expect(screen.getAllByText(/退审/).length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("保存")).toBeNull();
    const editor = screen.getByTestId("curated-json-editor");
    expect(editor).toHaveProperty("readOnly", true);
  });
});
