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

function mockDialogResources() {
  server.onGet("/projects/p1/prompt-templates/?page=1&page_size=100", {
    items: [tpl], total: 1, page: 1, page_size: 100,
  });
  server.onGet("/projects/p1/model-configs/?page=1&page_size=100", {
    items: [model], total: 1, page: 1, page_size: 100,
  });
  server.onGet("/projects/p1/documents/d1/chunks?page=1&page_size=200&status=ready", {
    items: [
      { id: "c1", section_id: "s1", document_id: "d1", ordinal: 0, heading_path: "", content: "a", token_count: 1, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 },
    ],
    total: 1,
    page: 1,
    page_size: 200,
  });
}

function batchResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: "batch-restored",
    document_id: "d1",
    chunk_set_id: "set-1",
    model_config_id: "m1",
    prompt_template_id: "t1",
    selected_chunk_ids: ["c1"],
    status: "completed",
    total_chunks: 1,
    completed_chunks: 1,
    summary_json: {
      succeeded: 1,
      failed: 0,
      cancelled: 0,
      candidate_ids: ["cand-1"],
      failures: [],
    },
    created_by: "u1",
    is_legacy: false,
    provenance_status: "verified",
    provenance_error_code: null,
    renderer_version: "prompt-renderer:v1",
    retry_of_generation_batch_id: null,
    ...overrides,
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/projects/p1/documents/d1");
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

  it("单条提交网络失败后复用同一个 Idempotency-Key", async () => {
    server.onGet("/chunks/c1", { id: "c1", section_id: "s1", document_id: "d1", ordinal: 0, heading_path: "1", content: "内容", token_count: 10, status: "ready", chunk_set_id: "set-1", chunk_set_version: 1 });
    server.onGet("/projects/p1/prompt-templates/?page=1&page_size=100", { items: [tpl], total: 1, page: 1, page_size: 100 });
    server.onGet("/projects/p1/model-configs/?page=1&page_size=100", { items: [model], total: 1, page: 1, page_size: 100 });
    const submit = server.mock("POST", "/chunks/c1/generate", ({ callCount }) =>
      callCount === 1
        ? { networkError: true }
        : { body: { task_id: "task-replayed", generation_batch_id: "batch-replayed", status: "queued" } },
    );

    render(<ChunkDetailPage />);
    await screen.findByText("分块 #1");
    const button = screen.getByRole("button", { name: "生成" }) as HTMLButtonElement;
    await userEvent.click(button);
    await waitFor(() => expect(button.disabled).toBe(false));
    await userEvent.click(button);
    await screen.findByText(/task-replayed/);

    const firstHeaders = submit.calls[0].options?.headers as Record<string, string>;
    const secondHeaders = submit.calls[1].options?.headers as Record<string, string>;
    expect(firstHeaders["Idempotency-Key"]).toMatch(/^gen-submit-/);
    expect(secondHeaders["Idempotency-Key"]).toBe(firstHeaders["Idempotency-Key"]);
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

  it("批量提交网络失败后复用同一个 Idempotency-Key", async () => {
    mockDialogResources();
    const submit = server.mock(
      "POST",
      "/projects/p1/documents/d1/generate-batch",
      ({ callCount }) => callCount === 1
        ? { networkError: true }
        : { body: { task_id: "batch-task-replayed", generation_batch_id: "batch-replayed", status: "queued" } },
    );
    server.onGet("/projects/p1/tasks/batch-task-replayed", {
      id: "batch-task-replayed",
      status: "processing",
      progress: 10,
    });

    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    const button = await screen.findByRole("button", { name: "发起批量生成" });
    await userEvent.click(button);
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    await userEvent.click(button);
    await screen.findByText(/batch-task-replayed/);

    const firstHeaders = submit.calls[0].options?.headers as Record<string, string>;
    const secondHeaders = submit.calls[1].options?.headers as Record<string, string>;
    expect(firstHeaders["Idempotency-Key"]).toMatch(/^gen-submit-/);
    expect(secondHeaders["Idempotency-Key"]).toBe(firstHeaders["Idempotency-Key"]);
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

  it("从 URL 恢复 Task/Batch 并展示全成功终态", async () => {
    window.history.replaceState(
      null,
      "",
      "/projects/p1/documents/d1?task_id=task-restored&generation_batch_id=batch-restored",
    );
    mockDialogResources();
    server.onGet("/projects/p1/tasks/task-restored", {
      id: "task-restored",
      status: "completed",
      progress: 100,
      can_cancel: false,
      error_code: null,
      error_message: null,
    });
    server.onGet(
      "/projects/p1/generation-batches/batch-restored",
      batchResponse(),
    );
    const onGenerated = vi.fn();

    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
        onGenerated={onGenerated}
      />,
    );

    await screen.findByText(/成功 1 \/ 失败 0 \/ 取消 0/);
    expect(screen.getByText(/task-restored/)).toBeTruthy();
    expect(onGenerated).toHaveBeenCalledTimes(1);
  });

  it("提交期间阻止重复点击，并可取消 processing Task", async () => {
    mockDialogResources();
    const submit = server.onPost(
      "/projects/p1/documents/d1/generate-batch",
      { task_id: "task-cancel", generation_batch_id: "batch-cancel", status: "queued" },
      { delay: 30 },
    );
    let cancellationRequested = false;
    server.mock("GET", "/projects/p1/tasks/task-cancel", () => ({
      body: {
        id: "task-cancel",
        status: cancellationRequested ? "cancelling" : "processing",
        progress: 20,
        can_cancel: !cancellationRequested,
        error_code: null,
        error_message: null,
      },
    }));
    const cancel = server.mock("POST", "/projects/p1/tasks/task-cancel/cancel", () => {
      cancellationRequested = true;
      return {
        body: {
          id: "task-cancel",
          status: "cancelling",
          state_version: 2,
          completed_at: null,
        },
      };
    });

    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    const submitButton = await screen.findByRole("button", { name: "发起批量生成" });
    await Promise.all([
      userEvent.click(submitButton),
      userEvent.click(submitButton),
    ]);
    await screen.findByText("processing");
    expect(submit.callCount).toBe(1);

    await userEvent.click(screen.getByRole("button", { name: "取消任务" }));
    await screen.findByText("cancelling");
    expect(cancel.callCount).toBe(1);
  });

  it("失败批次 retry 后切换并持久化新的 Task/Batch", async () => {
    window.history.replaceState(
      null,
      "",
      "/projects/p1/documents/d1?task_id=task-failed&generation_batch_id=batch-failed",
    );
    mockDialogResources();
    server.onGet("/projects/p1/tasks/task-failed", {
      id: "task-failed",
      status: "failed",
      progress: 50,
      can_cancel: false,
      error_code: "MODEL_RATE_LIMITED",
      error_message: "rate limited",
    });
    server.onGet(
      "/projects/p1/generation-batches/batch-failed",
      batchResponse({ id: "batch-failed", status: "failed" }),
    );
    const retry = server.onPost(
      "/projects/p1/generation-batches/batch-failed/retry",
      { task_id: "task-new", generation_batch_id: "batch-new", status: "queued" },
    );

    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    await screen.findByText(/MODEL_RATE_LIMITED/);
    await userEvent.click(screen.getByRole("button", { name: "重试失败项" }));
    await screen.findByText(/task-new/);
    expect(screen.getByText(/batch-new/)).toBeTruthy();
    expect(retry.callCount).toBe(1);
    expect(window.location.search).toContain("task_id=task-new");
    expect(window.location.search).toContain("generation_batch_id=batch-new");
  });

  it("provenance 异常展示稳定错误码并禁止 retry", async () => {
    window.history.replaceState(
      null,
      "",
      "/projects/p1/documents/d1?task_id=task-invalid&generation_batch_id=batch-invalid",
    );
    mockDialogResources();
    server.onGet("/projects/p1/tasks/task-invalid", {
      id: "task-invalid",
      status: "failed",
      progress: 0,
      can_cancel: false,
      error_code: "GENERATION_PROVENANCE_INVALID",
      error_message: "snapshot hash mismatch",
    });
    server.onGet(
      "/projects/p1/generation-batches/batch-invalid",
      batchResponse({
        id: "batch-invalid",
        status: "failed",
        provenance_status: "invalid",
        provenance_error_code: "SNAPSHOT_HASH_MISMATCH",
      }),
    );

    render(
      <BatchGenerateDialog
        projectId="p1"
        docId="d1"
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    await screen.findByText(/GENERATION_PROVENANCE_INVALID/);
    expect(screen.getByText(/SNAPSHOT_HASH_MISMATCH/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试失败项" })).toBeNull();
  });
});
