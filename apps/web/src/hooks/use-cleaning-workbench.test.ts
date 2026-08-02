import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { useCleaningWorkbench, isBusinessError } from "@/hooks/use-cleaning-workbench";
import type { ApiErrorException } from "@/lib/api";

const sectionA = "section-a";
const sectionB = "section-b";

function makeSection(id: string, revision: number, cleaned: string) {
  return {
    id,
    cleaning_job_id: "job-1",
    document_id: "doc-1",
    ordinal: 0,
    heading_path: id === sectionA ? "A" : "B",
    source_pages: null,
    raw_markdown: cleaned,
    cleaned_markdown: cleaned,
    content_revision: revision,
    status: "in_cleaning",
    cleaned_by: null,
    assignment_status: "in_progress",
    assigned_to: null,
    assigned_by: null,
    assigned_at: null,
    completed_at: null,
    return_reason: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    lease: null,
  };
}

function makeLease(id: string, sectionId: string) {
  return {
    id,
    section_id: sectionId,
    user_id: "user-1",
    acquired_at: "2026-01-01T00:00:00Z",
    expires_at: "2026-01-01T00:10:00Z",
    released_at: null,
  };
}

describe("useCleaningWorkbench", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
  });

  afterEach(() => {
    server.restore();
    vi.clearAllMocks();
  });
  it("A→B 快速切换且 A 响应最后到达时，编辑器最终只显示 B（代次保护）", async () => {
    // A 的详情/acquire 延迟返回，B 立即返回；切换后 A 的迟到响应不得覆盖 B。
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 0, "内容A"), { delay: 300 });
    server.onGet(`/sections/${sectionA}/comments`, [], { delay: 300 });
    server.onPost(`/sections/${sectionA}/lease/acquire`, makeLease("lease-a", sectionA), { delay: 300 });
    server.onGet(`/sections/${sectionB}`, makeSection(sectionB, 0, "内容B"));
    server.onGet(`/sections/${sectionB}/comments`, []);
    server.onPost(`/sections/${sectionB}/lease/acquire`, makeLease("lease-b", sectionB));

    const { result } = renderHook(() => useCleaningWorkbench());

    await act(async () => {
      result.current.switchSection(sectionA);
    });
    // 立即切换到 B：取消 A 的未决请求并推进代次。
    await act(async () => {
      result.current.switchSection(sectionB);
    });

    // B 的响应（无延迟）应已应用。
    await waitFor(() => {
      expect(result.current.selectedSection?.id).toBe(sectionB);
    });

    // 等待 A 的迟到响应到达（若未被代次保护，会覆盖为 A）。
    await new Promise((r) => setTimeout(r, 350));

    // 编辑器只显示 B：selectedSection 仍为 B，editedMarkdown 为 B 的内容。
    expect(result.current.selectedSection?.id).toBe(sectionB);
    expect(result.current.editedMarkdown).toBe("内容B");
  });

  it("保存携带 revision + lease_id；版本冲突保留本地文本并设置 conflictState", async () => {
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 3, "服务端基线"));
    server.onGet(`/sections/${sectionA}/comments`, []);
    server.onPost(`/sections/${sectionA}/lease/acquire`, makeLease("lease-a", sectionA));
    // PATCH 返回 409 SECTION_VERSION_CONFLICT with current_revision=5。
    server.mock("PATCH", `/sections/${sectionA}`, {
      status: 409,
      body: {
        code: "SECTION_VERSION_CONFLICT",
        message: "内容版本已变化",
        context: { current_revision: 5 },
      },
    });

    const { result } = renderHook(() => useCleaningWorkbench());
    await act(async () => {
      result.current.switchSection(sectionA);
    });
    await waitFor(() => expect(result.current.lease).not.toBeNull());

    await act(async () => {
      result.current.setEditedMarkdown("我的本地文本");
    });
    const saveResult = await act(async () => result.current.save());

    expect(saveResult.currentRevision).toBe(5);
    expect(result.current.conflictState?.currentRevision).toBe(5);
    // 本地文本保留（不被自动覆盖）。
    expect(result.current.editedMarkdown).toBe("我的本地文本");
  });

  it("保存成功时原子更新基线/revision，isDirty 归位", async () => {
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 1, "v1"));
    server.onGet(`/sections/${sectionA}/comments`, []);
    server.onPost(`/sections/${sectionA}/lease/acquire`, makeLease("lease-a", sectionA));
    server.mock("PATCH", `/sections/${sectionA}`, {
      status: 200,
      body: makeSection(sectionA, 2, "v2"),
    });

    const { result } = renderHook(() => useCleaningWorkbench());
    await act(async () => {
      result.current.switchSection(sectionA);
    });
    await waitFor(() => expect(result.current.lease).not.toBeNull());

    await act(async () => {
      result.current.setEditedMarkdown("v2");
    });
    expect(result.current.isDirty).toBe(true);

    await act(async () => result.current.save());
    // 保存成功后 revision 推进为 2，dirty 归位。
    expect(result.current.savedRevision).toBe(2);
    expect(result.current.isDirty).toBe(false);
  });

  it("租约丢失时 heartbeat 失败切只读并保留草稿", async () => {
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 0, "基线"));
    server.onGet(`/sections/${sectionA}/comments`, []);
    server.onPost(`/sections/${sectionA}/lease/acquire`, makeLease("lease-a", sectionA));
    // heartbeat 返回 409 SECTION_LEASE_LOST（被他人抢走）。
    server.mock("POST", `/sections/${sectionA}/lease/heartbeat`, {
      status: 409,
      body: { code: "SECTION_LEASE_LOST", message: "租约已失效" },
    });

    const { result } = renderHook(() => useCleaningWorkbench());
    await act(async () => {
      result.current.switchSection(sectionA);
    });
    await waitFor(() => expect(result.current.lease).not.toBeNull());

    await act(async () => {
      result.current.setEditedMarkdown("我的草稿");
    });
    await act(async () => {
      await result.current.heartbeat();
    });

    // 心跳失败 -> 租约丢失 -> 只读，但草稿保留。
    expect(result.current.leaseLost).toBe(true);
    expect(result.current.lease).toBeNull();
    expect(result.current.editedMarkdown).toBe("我的草稿");
  });

  it("effect cleanup 只释放其捕获的 lease_id（切换后不释放新 lease）", async () => {
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 0, "A"));
    server.onGet(`/sections/${sectionA}/comments`, []);
    server.onPost(`/sections/${sectionA}/lease/acquire`, makeLease("lease-a", sectionA));
    server.onPost(`/sections/${sectionA}/lease/release`, {});
    server.onGet(`/sections/${sectionB}`, makeSection(sectionB, 0, "B"));
    server.onGet(`/sections/${sectionB}/comments`, []);
    server.onPost(`/sections/${sectionB}/lease/acquire`, makeLease("lease-b", sectionB));
    server.onPost(`/sections/${sectionB}/lease/release`, {});

    const { result, unmount } = renderHook(() => useCleaningWorkbench());
    await act(async () => {
      result.current.switchSection(sectionA);
    });
    await waitFor(() => expect(result.current.lease?.id).toBe("lease-a"));

    // 切到 B：A 的 cleanup 只释放 lease-a。
    await act(async () => {
      result.current.switchSection(sectionB);
    });
    await waitFor(() => expect(result.current.lease?.id).toBe("lease-b"));

    const releaseA = server.getHandler("POST", `/sections/${sectionA}/lease/release`);
    // A 的 release 应被调用（cleanup 释放其捕获的 lease-a）。
    await waitFor(() => expect(releaseA?.callCount).toBeGreaterThan(0));

    // 卸载：B 的 cleanup 释放 lease-b；B 的 release 被调用。
    await act(async () => {
      unmount();
    });
    const releaseB = server.getHandler("POST", `/sections/${sectionB}/lease/release`);
    await waitFor(() => expect(releaseB?.callCount).toBeGreaterThan(0));
  });

  it("isBusinessError 按稳定 code 判别", () => {
    const err = new Error("x") as ApiErrorException;
    Object.defineProperty(err, "apiError", {
      value: { kind: "business", status: 409, code: "SECTION_LEASE_LOST", message: "lost", context: null },
    });
    expect(isBusinessError(err, "SECTION_LEASE_LOST")).toBe(true);
    expect(isBusinessError(err, "SECTION_VERSION_CONFLICT")).toBe(false);
  });
});
