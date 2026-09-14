"use client";

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { TokenStore } from "@/lib/auth";
import ExportsPage from "./page";

const server = createApiMockServer();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    variant,
    size,
    ...props
  }: React.ComponentProps<"button"> & { variant?: string; size?: string }) => {
    void variant;
    void size;
    return <button {...props}>{children}</button>;
  },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const completedExport = {
  id: "e1",
  project_id: "p1",
  dataset_id: "d1",
  benchmark_id: null,
  source_type: "dataset",
  export_profile_id: "ep1",
  format: "qa_json",
  status: "completed",
  item_count: 2,
  output_sha256: "a".repeat(64),
  file_size: 128,
  integrity_status: "verified",
  task_id: "task-old",
  retry_count: 0,
  error_code: null,
  error_message: null,
  is_legacy: false,
  created_by: "u1",
  created_at: "2026-08-03T00:00:00Z",
  completed_at: "2026-08-03T00:01:00Z",
  snapshot_manifest_id: "sm1",
};

const failedExport = {
  ...completedExport,
  id: "e2",
  status: "failed",
  integrity_status: "pending",
  item_count: null,
  output_sha256: null,
  file_size: null,
  task_id: "task-failed",
  error_code: "NETWORK_ERROR",
  error_message: "timeout",
  completed_at: "2026-08-03T00:02:00Z",
  snapshot_manifest_id: "sm2",
};

function listBody(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/projects/p1/exports");
  localStorage.clear();
  TokenStore.setTokens("access-1", "refresh-1");
  server.reset();
  server.install();
  vi.clearAllMocks();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

afterEach(() => {
  server.restore();
  vi.restoreAllMocks();
});

describe("导出页", () => {
  it("用 Bearer 签发瞬时下载链接，并在验证失败后禁用下载", async () => {
    server.onGet("/projects/p1/exports/?page=1&page_size=20", listBody([completedExport]));
    const download = server.onPost(
      "/projects/p1/exports/e1/download-link",
      {
        url: "http://minio.test/object?versionId=v1&X-Amz-Signature=secret",
        expires_at: "2026-08-03T00:05:00Z",
        filename: "export-e1.json",
      },
      { delay: 25 },
    );
    server.onPost("/projects/p1/exports/e1/verify?deep=false", {
      export_id: "e1",
      status: "failed",
      shallow: {
        db_fields_present: true,
        version_id_present: true,
        metadata_ok: false,
      },
      deep: null,
    });

    render(<ExportsPage />);
    await screen.findByText("已完成");
    await userEvent.click(screen.getByRole("button", { name: "下载" }));
    expect(screen.getByRole("button", { name: "签发中" })).toBeDisabled();
    await waitFor(() => expect(download.callCount).toBe(1));
    expect(download.calls[0].options?.headers).toMatchObject({
      Authorization: "Bearer access-1",
    });
    expect(TokenStore.getAccessToken()).toBe("access-1");
    expect(localStorage.getItem("download_url")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "浅验" }));
    await screen.findByText("验证失败");
    expect(screen.getByRole("button", { name: "下载" })).toBeDisabled();
  });

  it("展示深度验证逐项汇总", async () => {
    server.onGet("/projects/p1/exports/?page=1&page_size=20", listBody([completedExport]));
    server.onPost("/projects/p1/exports/e1/verify?deep=true", {
      export_id: "e1",
      status: "verified",
      shallow: {
        db_fields_present: true,
        version_id_present: true,
        metadata_ok: true,
      },
      deep: [
        { item: "payload_sha256", ok: true, detail: null },
        { item: "manifest_sha256", ok: true, detail: null },
        { item: "manifest_canonical_sha256", ok: true, detail: null },
      ],
    });

    render(<ExportsPage />);
    await screen.findByText("已完成");
    await userEvent.click(screen.getByRole("button", { name: "深验" }));
    await screen.findByText("深验 3/3");
  });

  it("重试超时后复用同一幂等键，并跟踪同一 Export 的新 Task", async () => {
    server.mock("GET", "/projects/p1/exports/?page=1&page_size=20", ({ callCount }) => ({
      body: listBody([
        callCount === 1
          ? failedExport
          : { ...failedExport, status: "queued", task_id: "task-new" },
      ]),
    }));
    const retry = server.mock(
      "POST",
      "/projects/p1/tasks/task-failed/retry",
      ({ callCount }) => callCount === 1
        ? { networkError: true }
        : { body: { id: "task-new", status: "queued" } },
    );

    render(<ExportsPage />);
    await screen.findByText("失败");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(retry.callCount).toBe(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "重试" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("排队中");

    expect(retry.callCount).toBe(2);
    const firstHeaders = retry.calls[0].options?.headers as Record<string, string>;
    const secondHeaders = retry.calls[1].options?.headers as Record<string, string>;
    expect(firstHeaders["Idempotency-Key"]).toMatch(/^export-retry-/);
    expect(secondHeaders["Idempotency-Key"]).toBe(firstHeaders["Idempotency-Key"]);
  });
});
