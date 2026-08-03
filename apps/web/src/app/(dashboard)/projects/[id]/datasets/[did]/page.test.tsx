"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { toast } from "sonner";
import DatasetDetailPage from "./page";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", did: "ds-1" }),
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

vi.mock("@/components/data-table", () => ({
  DataTable: <T,>({
    columns,
    data,
    total,
    page,
    pageSize,
    rowKey,
  }: {
    columns: { key: string; header: string; render: (row: T, idx: number) => React.ReactNode }[];
    data: T[];
    total: number;
    page: number;
    pageSize: number;
    rowKey: (row: T, idx: number) => string;
  }) => (
    <div data-testid="data-table">
      <div data-testid="table-summary">
        {total} items page {page}/{Math.max(1, Math.ceil(total / pageSize))}
      </div>
      {data.map((row, idx) => (
        <div key={rowKey(row, idx)} data-testid="table-row">
          {columns.map((col) => (
            <span key={col.key}>{col.render(row, idx)}</span>
          ))}
        </div>
      ))}
      {data.length === 0 && <div>empty</div>}
    </div>
  ),
}));

vi.mock("@/components/pagination", () => ({
  Pagination: () => <div data-testid="pagination" />,
}));

vi.mock("@/hooks/use-pagination", () => ({
  usePagination: () => ({ page: 1, pageSize: 20, setPage: vi.fn() }),
}));

vi.mock("@/components/eligible-item-picker", () => ({
  EligibleItemPicker: ({
    open,
    onOpenChange,
  }: {
    open: boolean;
    onOpenChange: (o: boolean) => void;
    onAdded?: () => void;
  }) =>
    open ? (
      <div data-testid="picker">
        <button onClick={() => onOpenChange(false)}>close-picker</button>
        <span>eligible-picker-content</span>
      </div>
    ) : null,
}));

const pinnedContent = {
  question: "什么是压比?",
  answer: "压比是出口与进口压力之比",
};

const datasetDetail = {
  id: "ds-1",
  project_id: "p1",
  name: "压比数据集",
  description: null,
  status: "draft",
  created_by: "u1",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  item_count: 1,
  composition_revision: 1,
  composition_sha256: "a".repeat(64),
  composition_canonicalization_version: "composition-cjson-v1",
  finalized_revision: null,
  finalized_sha256: null,
  finalized_canonicalization_version: null,
  finalized_by: null,
  finalized_at: null,
};

const itemDetail = {
  id: "item-1",
  container_id: "ds-1",
  curated_item_id: "ci-1",
  curated_revision_id: "rev-1",
  curated_revision_sha256: "b".repeat(64),
  approval_record_id: "rec-1",
  approval_evidence_sha256: "c".repeat(64),
  ordinal: 1,
  created_at: "2026-08-02T00:00:00Z",
  curated_item: {
    id: "ci-1",
    item_type: "qa_generation",
    current_status: "approved",
    current_revision: 1,
    pinned_revision: { id: "rev-1", version: 1, content_sha256: "b".repeat(64) },
    pinned_content: pinnedContent,
    approved_at: "2026-08-02T00:00:00Z",
  },
};

function defaultRoutes(meRole = "editor") {
  server.onGet("/auth/me", { id: "u1", username: "editor", email: "e@x", role: meRole });
  server.onGet("/projects/p1/datasets/ds-1", datasetDetail);
  server.onGet("/projects/p1/datasets/ds-1/items?page=1&page_size=20", {
    items: [itemDetail],
    total: 1,
    page: 1,
    page_size: 20,
  });
  server.onGet("/projects/p1/export-profiles/?page=1&page_size=50", {
    items: [{ id: "ep-1", name: "SFT", format: "sft_jsonl" }],
    total: 1,
    page: 1,
    page_size: 50,
  });
}

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("Dataset 详情页", () => {
  it("editor 加载数据集详情并渲染条目预览与添加按钮", async () => {
    defaultRoutes();
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    // 条目预览使用 pinned_content.question。
    expect(screen.getByText(/什么是压比/)).toBeTruthy();
    expect(screen.getByText("添加条目")).toBeTruthy();
    expect(screen.getByText(/rev 1 · aaaaaaaa/)).toBeTruthy();
  });

  it("editor 移除条目：确认后 DELETE，成功后刷新", async () => {
    defaultRoutes();
    server.mock("DELETE", "/projects/p1/datasets/ds-1/items/item-1", {
      status: 204,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    await userEvent.click(screen.getByText("移除"));
    expect(window.confirm).toHaveBeenCalled();
    expect(server.wasCalled("DELETE", "/projects/p1/datasets/ds-1/items/item-1")).toBe(true);
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("已移除");
    });
  });

  it("viewer 不显示添加/移除控件", async () => {
    defaultRoutes("viewer");
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    expect(screen.queryByText("添加条目")).toBeNull();
    expect(screen.queryByText("移除")).toBeNull();
  });

  it("finalized 容器只读并展示 finalized 摘要", async () => {
    server.onGet("/auth/me", { id: "u1", username: "editor", email: "e@x", role: "reviewer" });
    server.onGet("/projects/p1/datasets/ds-1", {
      ...datasetDetail,
      status: "finalized",
      finalized_revision: 1,
      finalized_sha256: "d".repeat(64),
      finalized_canonicalization_version: "composition-cjson-v1",
      finalized_by: "u2",
      finalized_at: "2026-08-03T00:00:00Z",
    });
    server.onGet("/projects/p1/datasets/ds-1/items?page=1&page_size=20", {
      items: [itemDetail],
      total: 1,
      page: 1,
      page_size: 20,
    });
    server.onGet("/projects/p1/export-profiles/?page=1&page_size=50", {
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
    });
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    // reviewer 在 finalized 容器不应看到添加/冻结按钮（只读说明）。
    expect(screen.queryByText("添加条目")).toBeNull();
    expect(screen.queryByText("冻结")).toBeNull();
    expect(screen.getByText(/已冻结：rev 1/)).toBeTruthy();
  });

  it("reviewer 冻结对话框：confirm 后 POST finalize 携带 expected revision/hash", async () => {
    defaultRoutes("reviewer");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.onPost("/projects/p1/datasets/ds-1/finalize", {
      ...datasetDetail,
      status: "finalized",
      finalized_revision: 1,
      finalized_sha256: datasetDetail.composition_sha256,
      finalized_canonicalization_version: "composition-cjson-v1",
      finalized_by: "u2",
      finalized_at: "2026-08-03T00:00:00Z",
    });
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    await userEvent.click(screen.getByText("冻结"));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(server.wasCalled("POST", "/projects/p1/datasets/ds-1/finalize")).toBe(true);
    });
    const finalizeCall = server.getHandler("POST", "/projects/p1/datasets/ds-1/finalize");
    expect(finalizeCall?.calls[0].options?.body).toContain("expected_revision");
    expect(finalizeCall?.calls[0].options?.body).toContain("expected_sha256");
  });

  it("添加按钮打开 picker（共享组件单独测试）", async () => {
    defaultRoutes();
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    await userEvent.click(screen.getByText("添加条目"));
    await waitFor(() => {
      expect(screen.getByTestId("picker")).toBeTruthy();
    });
  });

  it("移除失败：错误父子组合 404 不改变本地列表", async () => {
    defaultRoutes();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.mock("DELETE", "/projects/p1/datasets/ds-1/items/item-1", {
      status: 404,
      body: { code: "NOT_FOUND", message: "数据集条目不存在" },
    });
    render(<DatasetDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比数据集")).toBeTruthy();
    });
    await userEvent.click(screen.getByText("移除"));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("移除失败");
    });
  });
});
