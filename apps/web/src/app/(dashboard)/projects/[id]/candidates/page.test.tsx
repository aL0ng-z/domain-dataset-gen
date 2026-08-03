"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { toast } from "sonner";
import CandidatesPage from "./page";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
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
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const candidate = {
  id: "cand-1",
  generation_run_id: "run-1",
  chunk_id: "chunk-1",
  content: { question: "什么是压比?", answer: "压比是出口与进口压力之比" },
  candidate_type: "qa_generation",
  status: "ai_generated",
  reviewed_by: null,
  review_verdict: null,
  review_evidence_spans: null,
  reject_reason: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const chunk = {
  id: "chunk-1",
  section_id: "s1",
  document_id: "d1",
  ordinal: 0,
  heading_path: "1.1",
  content: "压比是出口与进口压力之比，是衡量压缩机性能的核心指标。",
  source_pages: { start: 1 },
  token_count: 20,
  status: "generated",
  chunk_set_id: "set-1",
  chunk_set_version: 1,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("候选审核页", () => {
  it("列表加载 object content 不触发对象渲染异常", async () => {
    server.onGet(
      "/candidates?project_id=p1&page=1&page_size=20",
      { items: [candidate], total: 1, page: 1, page_size: 20 },
    );

    render(<CandidatesPage />);

    await waitFor(() => {
      expect(screen.getByText(/压比/)).toBeTruthy();
    });
  });

  it("展开审核面板可编辑 JSON；非法 JSON 留在本地不发 PATCH", async () => {
    server.onGet(
      "/candidates?project_id=p1&page=1&page_size=20",
      { items: [candidate], total: 1, page: 1, page_size: 20 },
    );
    server.onGet("/chunks/chunk-1", chunk);

    render(<CandidatesPage />);

    await waitFor(() => {
      expect(screen.getByText(/压比/)).toBeTruthy();
    });

    // 展开
    await userEvent.click(screen.getAllByRole("button")[0]);

    const editor = await screen.findByTestId("candidate-json-editor");
    // 修改为非法 JSON（用 fireEvent 避免 userEvent 解析 { 特殊键）
    const { fireEvent } = require("@testing-library/react");
    await userEvent.clear(editor);
    fireEvent.change(editor, { target: { value: "{ not valid json" } });

    expect(screen.getByTestId("candidate-json-editor")).toBeTruthy();
    // 不发出 PATCH
    await userEvent.click(screen.getByText("保存编辑"));
    expect(
      server.getHandler("PATCH", "/candidates/cand-1")?.callCount ?? 0,
    ).toBe(0);
  });

  it("supported 判定缺少证据时显示错误提示（409 code 分支）", async () => {
    server.onGet(
      "/candidates?project_id=p1&page=1&page_size=20",
      { items: [candidate], total: 1, page: 1, page_size: 20 },
    );
    server.onGet("/chunks/chunk-1", chunk);
    server.mock("POST", "/candidates/cand-1/review", {
      status: 409,
      body: {
        code: "CANDIDATE_EVIDENCE_REQUIRED",
        message: "supported/partially_supported 必须提供有效证据 span",
      },
    });

    render(<CandidatesPage />);

    await waitFor(() => {
      expect(screen.getByText(/压比/)).toBeTruthy();
    });

    await userEvent.click(screen.getAllByRole("button")[0]);
    await screen.findByTestId("candidate-json-editor");

    await userEvent.click(screen.getByTestId("submit-review"));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("支持判定必须提供证据");
    });
  });
});
