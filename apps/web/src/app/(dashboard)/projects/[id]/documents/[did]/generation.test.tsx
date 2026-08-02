"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import ChunkDetailPage from "./chunks/[cid]/page";
import { BatchGenerateDialog } from "@/components/batch-generate-dialog";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", did: "d1", cid: "c1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/status-badge", () => ({
  StatusBadge: ({ status }: { status: string }) => <span data-testid="badge">{status}</span>,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: React.ComponentProps<"button"> & { variant?: string; size?: string }) => (
    <button {...props}>{children}</button>
  ),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div data-testid="dialog">{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const tpl = { id: "t1", project_id: "p1", task_type: "qa_generation", name: "QA 模板", version: 1, system_prompt: "s", user_prompt_template: "{{content}}", input_schema: null, output_schema: null, is_default: true };
const model = { id: "m1", project_id: "p1", name: "GPT", version: 1, is_default: true, provider: "openai", base_url: "http://x", model_name: "gpt-4o", temperature: 0.7, max_tokens: 512, extra_params: null };

beforeEach(() => {
  server.reset();
  server.install();
  vi.clearAllMocks();
});

afterEach(() => {
  server.restore();
});

describe("单 Chunk 生成页", () => {
  it("加载模板与模型，缺一禁用生成；202 后显示 queued 而非生成完成提示", async () => {
    server.onGet("/chunks/c1", { id: "c1", section_id: "s1", document_id: "d1", ordinal: 0, heading_path: "1", content: "内容", token_count: 10, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 });
    server.onGet("/projects/p1/prompt-templates/?page=1&page_size=100", { items: [tpl], total: 1, page: 1, page_size: 100 });
    server.onGet("/projects/p1/model-configs/?page=1&page_size=100", { items: [model], total: 1, page: 1, page_size: 100 });
    server.onPost("/chunks/c1/generate", { task_id: "task-1", generation_batch_id: "batch-1", status: "queued" });

    render(<ChunkDetailPage />);
    await screen.findByText("分块 #1");
    // 模板与模型均已选择。
    expect(screen.getByLabelText("模板")).toBeTruthy();
    expect(screen.getByLabelText("模型")).toBeTruthy();

    // 点击生成 -> 202 后显示 queued。
    await userEvent.click(screen.getByText("生成"));
    await waitFor(() => {
      expect(screen.getByText(/生成状态/)).toBeTruthy();
      expect(screen.getByText("queued")).toBeTruthy();
    });
    // 不得提示"生成完成"。
    expect(screen.queryByText("生成完成")).toBeNull();
  });

  it("模板或模型缺失时生成按钮禁用", async () => {
    server.onGet("/chunks/c1", { id: "c1", section_id: "s1", document_id: "d1", ordinal: 0, heading_path: "1", content: "内容", token_count: 10, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 });
    server.onGet("/projects/p1/prompt-templates/?page=1&page_size=100", { items: [], total: 0, page: 1, page_size: 100 });
    server.onGet("/projects/p1/model-configs/?page=1&page_size=100", { items: [model], total: 1, page: 1, page_size: 100 });

    render(<ChunkDetailPage />);
    await screen.findByText("分块 #1");
    // 无模板 -> 生成禁用，显示配置入口提示。
    const genBtn = screen.getByText("生成");
    expect((genBtn as HTMLButtonElement).disabled).toBe(true);
    await screen.findByText(/缺少模板或模型配置/);
  });
});

describe("批量生成对话框", () => {
  it("选择模板/模型/范围后提交；202 后跟踪 task/batch 状态", async () => {
    server.onGet("/projects/p1/prompt-templates/?page=1&page_size=100", { items: [tpl], total: 1, page: 1, page_size: 100 });
    server.onGet("/projects/p1/model-configs/?page=1&page_size=100", { items: [model], total: 1, page: 1, page_size: 100 });
    server.onGet("/projects/p1/documents/d1/chunks?page=1&page_size=200&status=ready", {
      items: [
        { id: "c1", section_id: "s1", document_id: "d1", ordinal: 0, heading_path: "", content: "a", token_count: 1, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 },
        { id: "c2", section_id: "s1", document_id: "d1", ordinal: 1, heading_path: "", content: "b", token_count: 1, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 },
      ],
      total: 2,
      page: 1,
      page_size: 200,
    });
    server.onPost("/projects/p1/documents/d1/generate-batch", { task_id: "btask-1", generation_batch_id: "bbatch-1", status: "queued" });
    // 轮询 parent task -> processing（未终态，继续轮询）。
    server.onGet("/projects/p1/tasks/btask-1", { id: "btask-1", status: "processing", progress: 50 });

    const onGen = vi.fn();
    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
        onGenerated={onGen}
      />,
    );

    // 默认选择模板/模型（is_default）。
    await screen.findByText("发起批量生成");
    const submitBtn = screen.getByText("发起批量生成") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(false);

    // 提交 -> 202 -> 显示 queued/processing。
    await userEvent.click(submitBtn);
    await waitFor(() => {
      expect(screen.getByText("processing")).toBeTruthy();
    });
    // 显示任务/批次 ID。
    expect(screen.getByText(/bbatch-1/)).toBeTruthy();
  });

  it("部分失败展示成功/失败/取消计数与失败明细", async () => {
    server.onGet("/projects/p1/prompt-templates/?page=1&page_size=100", { items: [tpl], total: 1, page: 1, page_size: 100 });
    server.onGet("/projects/p1/model-configs/?page=1&page_size=100", { items: [model], total: 1, page: 1, page_size: 100 });
    server.onGet("/projects/p1/documents/d1/chunks?page=1&page_size=200&status=ready", {
      items: [
        { id: "c1", section_id: "s1", document_id: "d1", ordinal: 0, heading_path: "", content: "a", token_count: 1, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 },
      ],
      total: 1,
      page: 1,
      page_size: 200,
    });
    server.onPost("/projects/p1/documents/d1/generate-batch", { task_id: "btask-2", generation_batch_id: "bbatch-2", status: "queued" });
    // 第一次轮询 processing，第二次 completed + batch 带部分失败 summary。
    server.mock("GET", "/projects/p1/tasks/btask-2", ({ callCount }) => {
      if (callCount === 1) {
        return { body: { id: "btask-2", status: "processing", progress: 60 } };
      }
      return {
        body: {
          id: "btask-2",
          status: "completed",
          progress: 100,
        },
      };
    });
    server.onGet("/projects/p1/generation-batches/bbatch-2", {
      id: "bbatch-2",
      document_id: "d1",
      chunk_set_id: "set-1",
      model_config_id: "m1",
      prompt_template_id: "t1",
      selected_chunk_ids: ["c1"],
      status: "failed",
      total_chunks: 2,
      completed_chunks: 1,
      summary_json: {
        succeeded: 1,
        failed: 1,
        cancelled: 0,
        candidate_ids: ["cand-1"],
        failures: [{ chunk_id: "c2", code: "RUN_FAILED", message: "LLM 返回非 JSON" }],
      },
      created_by: "u1",
      is_legacy: false,
      provenance_status: "verified",
      provenance_error_code: null,
      renderer_version: "prompt-renderer:v1",
      retry_of_generation_batch_id: null,
    });

    const onGen = vi.fn();
    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
        onGenerated={onGen}
      />,
    );
    await screen.findByText("发起批量生成");
    await userEvent.click(screen.getByText("发起批量生成") as HTMLButtonElement);

    // 轮询到 completed + batch 部分失败（第二轮间隔 3s，放宽等待）。
    await waitFor(
      () => {
        expect(screen.getByText(/成功 1 \/ 失败 1/)).toBeTruthy();
      },
      { timeout: 4000 },
    );
    // 失败明细可定位 chunk。
    expect(screen.getByText(/RUN_FAILED/)).toBeTruthy();
    expect(screen.getByText(/LLM 返回非 JSON/)).toBeTruthy();
  });
});
