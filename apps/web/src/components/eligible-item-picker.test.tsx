"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { toast } from "sonner";
import { EligibleItemPicker } from "./eligible-item-picker";

const server = createApiMockServer();

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    ...props
  }: React.ComponentProps<"button"> & { variant?: string; size?: string }) => (
    <button {...props}>{children}</button>
  ),
}));

// 简单桩：Dialog 直接渲染 children，简化测试。
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/components/pagination", () => ({
  Pagination: ({ total }: { total: number }) => <div data-testid="pagination">{total}</div>,
}));

const eligibleItem = {
  id: "ci-2",
  item_type: "qa_generation",
  current_status: "approved",
  current_revision: 1,
  pinned_revision: { id: "rev-2", version: 1, content_sha256: "e".repeat(64) },
  pinned_content: { question: "压比极限?", answer: "…" },
  approved_at: "2026-08-02T00:00:00Z",
};

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("EligibleItemPicker", () => {
  it("Dataset 模式：加载 eligible 列表、空态与搜索", async () => {
    server.onGet("/projects/p1/datasets/ds-1/eligible-items?page=1&page_size=20", {
      items: [eligibleItem],
      total: 1,
      page: 1,
      page_size: 20,
    });
    server.onGet("/projects/p1/datasets/ds-1/eligible-items?page=1&page_size=20&query=%E5%8E%8B%E6%AF%94", {
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });

    const onOpenChange = vi.fn();
    render(
      <EligibleItemPicker
        containerType="dataset"
        projectId="p1"
        containerId="ds-1"
        open
        onOpenChange={onOpenChange}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/压比极限/)).toBeTruthy();
    });
    // 资格说明区分 Dataset/Benchmark。
    expect(screen.getByText(/仅已批准且含证据/)).toBeTruthy();

    // 搜索。
    await userEvent.type(screen.getByPlaceholderText("搜索标题/摘要"), "压比");
    await userEvent.click(screen.getByText("搜索"));
    await waitFor(() => {
      expect(screen.getByText("没有匹配的可添加条目")).toBeTruthy();
    });
  });

  it("Benchmark 模式显示 supported 资格说明", async () => {
    server.onGet("/projects/p1/benchmarks/bm-1/eligible-items?page=1&page_size=20", {
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
    render(
      <EligibleItemPicker
        containerType="benchmark"
        projectId="p1"
        containerId="bm-1"
        open
        onOpenChange={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/仅已批准、含证据且 source Candidate 判定为 supported/)).toBeTruthy();
    });
  });

  it("添加成功后关闭并回调 onAdded", async () => {
    server.onGet("/projects/p1/datasets/ds-1/eligible-items?page=1&page_size=20", {
      items: [eligibleItem],
      total: 1,
      page: 1,
      page_size: 20,
    });
    server.mock("POST", "/projects/p1/datasets/ds-1/items", {
      status: 201,
      body: { id: "item-new", ordinal: 2 },
    });

    const onOpenChange = vi.fn();
    const onAdded = vi.fn();
    render(
      <EligibleItemPicker
        containerType="dataset"
        projectId="p1"
        containerId="ds-1"
        open
        onOpenChange={onOpenChange}
        onAdded={onAdded}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/压比极限/)).toBeTruthy();
    });
    await userEvent.click(screen.getByText("添加"));
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
      expect(onAdded).toHaveBeenCalled();
      expect(toast.success).toHaveBeenCalledWith("已添加条目");
    });
  });

  it("重复成员 409：提示并刷新 eligible 列表，不乐观插入", async () => {
    server.onGet("/projects/p1/datasets/ds-1/eligible-items?page=1&page_size=20", {
      items: [eligibleItem],
      total: 1,
      page: 1,
      page_size: 20,
    });
    server.mock("POST", "/projects/p1/datasets/ds-1/items", {
      status: 409,
      body: { code: "COMPOSITION_MEMBER_EXISTS", message: "该知识条目已在容器中" },
    });

    render(
      <EligibleItemPicker
        containerType="dataset"
        projectId="p1"
        containerId="ds-1"
        open
        onOpenChange={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/压比极限/)).toBeTruthy();
    });
    await userEvent.click(screen.getByText("添加"));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("该条目已在容器中，已刷新列表");
    });
    // eligible 列表被刷新（再次 GET）。
    expect(
      server.getHandler("GET", "/projects/p1/datasets/ds-1/eligible-items?page=1&page_size=20")
        ?.callCount,
    ).toBeGreaterThan(1);
  });

  it("空态：无 eligible 时显示空提示", async () => {
    server.onGet("/projects/p1/datasets/ds-1/eligible-items?page=1&page_size=20", {
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
    render(
      <EligibleItemPicker
        containerType="dataset"
        projectId="p1"
        containerId="ds-1"
        open
        onOpenChange={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("没有可添加条目")).toBeTruthy();
    });
  });
});
