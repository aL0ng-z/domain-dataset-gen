"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { toast } from "sonner";
import BenchmarkDetailPage from "./page";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", bid: "bm-1" }),
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
        {total} cases page {page}/{Math.max(1, Math.ceil(total / pageSize))}
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
  question: "压比定义?",
  answer: "压比是出口与进口压力之比",
};

const benchmarkDetail = {
  id: "bm-1",
  project_id: "p1",
  name: "压比基准集",
  description: null,
  status: "draft",
  created_by: "u1",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  case_count: 1,
  composition_revision: 1,
  composition_sha256: "a".repeat(64),
  composition_canonicalization_version: "composition-cjson-v1",
  finalized_revision: null,
  finalized_sha256: null,
  finalized_canonicalization_version: null,
  finalized_by: null,
  finalized_at: null,
};

const caseDetail = {
  id: "case-1",
  container_id: "bm-1",
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
  server.onGet("/projects/p1/access", { effective_role: meRole });
  server.onGet("/projects/p1/benchmarks/bm-1", benchmarkDetail);
  server.onGet("/projects/p1/benchmarks/bm-1/cases?page=1&page_size=20", {
    items: [caseDetail],
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
}

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("Benchmark 详情页", () => {
  it("editor 加载并渲染用例预览，退审后显示固定预览提示", async () => {
    defaultRoutes();
    render(<BenchmarkDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比基准集")).toBeTruthy();
    });
    expect(screen.getByText(/压比定义/)).toBeTruthy();
    expect(screen.getByText("添加用例")).toBeTruthy();
    expect(screen.getByText(/rev 1 · aaaaaaaa/)).toBeTruthy();
  });

  it("editor 移除用例：确认后 DELETE 成功", async () => {
    defaultRoutes();
    server.mock("DELETE", "/projects/p1/benchmarks/bm-1/cases/case-1", { status: 204 });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<BenchmarkDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比基准集")).toBeTruthy();
    });
    await userEvent.click(screen.getByText("移除"));
    expect(server.wasCalled("DELETE", "/projects/p1/benchmarks/bm-1/cases/case-1")).toBe(true);
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("已移除");
    });
  });

  it("条目退审后：当前 status 变化但 pinned preview 不变，显示修订提示", async () => {
    server.onGet("/projects/p1/access", { effective_role: "editor" });
    server.onGet("/projects/p1/benchmarks/bm-1", benchmarkDetail);
    server.onGet("/projects/p1/benchmarks/bm-1/cases?page=1&page_size=20", {
      items: [
        {
          ...caseDetail,
          curated_item: {
            ...caseDetail.curated_item,
            current_status: "draft",
            current_revision: 2,
          },
        },
      ],
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
    render(<BenchmarkDetailPage />);

    await waitFor(() => {
      expect(screen.getByText(/条目已修订到 v2/)).toBeTruthy();
    });
    // pinned preview 仍固定 v1 内容。
    expect(screen.getByText(/压比定义/)).toBeTruthy();
  });

  it("reviewer 冻结：POST finalize 携带 expected revision/hash；hash 异常禁止继续", async () => {
    defaultRoutes("reviewer");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.mock("POST", "/projects/p1/benchmarks/bm-1/finalize", {
      status: 409,
      body: {
        code: "COMPOSITION_HASH_INVALID",
        message: "保存 hash 无法复核",
      },
    });
    render(<BenchmarkDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("压比基准集")).toBeTruthy();
    });
    await userEvent.click(screen.getByText("冻结"));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(server.wasCalled("POST", "/projects/p1/benchmarks/bm-1/finalize")).toBe(true);
    });
    // hash 异常不得允许忽略后继续 finalize：不触发成功提示。
    expect(toast.success).not.toHaveBeenCalled();
    const finalizeCall = server.getHandler("POST", "/projects/p1/benchmarks/bm-1/finalize");
    expect(finalizeCall?.calls[0].options?.body).toContain("expected_sha256");
  });
});
