import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    vi.useRealTimers();
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

    expect(saveResult).toMatchObject({ ok: false, reason: "conflict", currentRevision: 5 });
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

  async function openSection(delay = 0) {
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 1, "原文"));
    server.onGet(`/sections/${sectionA}/comments`, []);
    server.onPost(`/sections/${sectionA}/lease/acquire`, makeLease("lease-a", sectionA), { delay });
    server.onPost(`/sections/${sectionA}/lease/release`, {});
    const hook = renderHook(() => useCleaningWorkbench());
    await act(async () => hook.result.current.switchSection(sectionA));
    return hook;
  }

  it("延迟 acquire 后自动续租超过 300 秒，切换后停止旧租约心跳", async () => {
    vi.useFakeTimers();
    const heartbeatA = server.onPost(`/sections/${sectionA}/lease/heartbeat`, makeLease("lease-a", sectionA));
    const { result } = await openSection(100);
    expect(result.current.lease).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(result.current.lease?.id).toBe("lease-a");
    await act(async () => vi.advanceTimersByTimeAsync(330_000));
    expect(heartbeatA.callCount).toBe(11);
    server.mock("PATCH", `/sections/${sectionA}`, { body: makeSection(sectionA, 2, "长时间编辑") });
    await act(async () => result.current.setEditedMarkdown("长时间编辑"));
    expect(await act(async () => result.current.save())).toMatchObject({ ok: true, revision: 2 });
    server.onGet(`/sections/${sectionB}`, makeSection(sectionB, 0, "B"));
    server.onGet(`/sections/${sectionB}/comments`, []);
    server.onPost(`/sections/${sectionB}/lease/acquire`, makeLease("lease-b", sectionB));
    server.onPost(`/sections/${sectionB}/lease/heartbeat`, makeLease("lease-b", sectionB));
    await act(async () => result.current.switchSection(sectionB));
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(heartbeatA.callCount).toBe(11);
  });

  it.each([
    { status: 409, body: { code: "SECTION_VERSION_CONFLICT", message: "changed", context: { current_revision: 4 } }, reason: "conflict" },
    { status: 409, body: { code: "SECTION_LEASE_LOST", message: "lost" }, reason: "lease_lost" },
    { status: 500, body: {}, reason: "request_failed" },
    { networkError: true, reason: "request_failed" },
  ])("保存失败 ($reason) 返回明确失败且不提交，原章节和草稿保留", async ({ reason, ...response }) => {
    const { result } = await openSection();
    await waitFor(() => expect(result.current.lease).not.toBeNull());
    server.mock("PATCH", `/sections/${sectionA}`, response);
    const submit = server.onPost(`/sections/${sectionA}/submit`, {});
    await act(async () => result.current.setEditedMarkdown("不可丢失的草稿"));
    const saved = await act(async () => result.current.save());
    expect(saved).toMatchObject({ ok: false, reason });
    expect(await act(async () => result.current.submit())).toBe(false);
    expect(submit.callCount).toBe(0);
    expect(result.current.selectedSectionId).toBe(sectionA);
    expect(result.current.editedMarkdown).toBe("不可丢失的草稿");
    expect(result.current.isDirty).toBe(true);
  });

  it("无租约保存明确失败", async () => {
    const { result } = renderHook(() => useCleaningWorkbench());
    expect(await act(async () => result.current.save())).toMatchObject({ ok: false });
  });

  it("编辑后立即提交先保存最新正文，再提交返回的 revision，重复提交只请求一次", async () => {
    const { result } = await openSection();
    await waitFor(() => expect(result.current.lease).not.toBeNull());
    const order: string[] = [];
    const patch = server.mock("PATCH", `/sections/${sectionA}`, ({ init }) => {
      order.push("save");
      expect(JSON.parse(init?.body as string)).toMatchObject({ cleaned_markdown: "最后正文", expected_revision: 1 });
      return { body: makeSection(sectionA, 2, "最后正文"), delay: 30 };
    });
    server.mock("POST", `/sections/${sectionA}/submit`, ({ init }) => {
      order.push("submit");
      expect(JSON.parse(init?.body as string)).toMatchObject({ expected_revision: 2, lease_id: "lease-a" });
      return { body: { ...makeSection(sectionA, 2, "最后正文"), status: "review_pending" } };
    });
    await act(async () => {
      result.current.setEditedMarkdown("最后正文");
      const pending = result.current.submit();
      result.current.setEditedMarkdown("提交过程中不能修改");
      expect(await result.current.submit()).toBe(false);
      expect(await pending).toBe(true);
    });
    expect(order).toEqual(["save", "submit"]);
    expect(patch.callCount).toBe(1);
    expect(result.current.editedMarkdown).toBe("最后正文");
    expect(result.current.lease).toBeNull();
  });

  it("自动保存、手动保存和提交交错时串行推进 revision，新增输入保持未保存直至下一次响应", async () => {
    vi.useFakeTimers();
    const { result } = await openSection();
    const requests: { cleaned_markdown: string; expected_revision: number }[] = [];
    server.mock("PATCH", `/sections/${sectionA}`, ({ init }) => {
      const body = JSON.parse(init?.body as string);
      requests.push(body);
      return { body: makeSection(sectionA, body.expected_revision + 1, body.cleaned_markdown), delay: 100 };
    });
    const submitted = server.onPost(`/sections/${sectionA}/submit`, makeSection(sectionA, 3, "v3"));
    await act(async () => result.current.setEditedMarkdown("v2"));
    await act(async () => vi.advanceTimersByTimeAsync(1200));
    expect(requests).toHaveLength(1);
    await act(async () => result.current.setEditedMarkdown("v3"));
    let manual!: ReturnType<typeof result.current.save>;
    let submit!: ReturnType<typeof result.current.submit>;
    await act(async () => {
      manual = result.current.save();
      submit = result.current.submit();
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(result.current.isDirty).toBe(true);
    expect(result.current.savedRevision).toBe(2);
    expect(submitted.callCount).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
      expect(await manual).toMatchObject({ ok: true, revision: 3 });
      expect(await submit).toBe(true);
    });
    expect(requests).toMatchObject([
      { cleaned_markdown: "v2", expected_revision: 1 },
      { cleaned_markdown: "v3", expected_revision: 2 },
    ]);
    expect(JSON.parse(submitted.calls[0].options?.body as string).expected_revision).toBe(3);
    expect(result.current.isDirty).toBe(false);
  });

  it("重新获取租约保留草稿，服务端版本已变化时暂停自动覆盖", async () => {
    const { result } = await openSection();
    await waitFor(() => expect(result.current.lease).not.toBeNull());
    await act(async () => result.current.setEditedMarkdown("本地草稿"));
    server.mock("POST", `/sections/${sectionA}/lease/heartbeat`, { status: 409, body: { code: "SECTION_LEASE_LOST", message: "lost" } });
    await act(async () => result.current.heartbeat());
    server.onGet(`/sections/${sectionA}`, makeSection(sectionA, 5, "其他人的新版"));
    await act(async () => result.current.relock());
    expect(result.current.editedMarkdown).toBe("本地草稿");
    expect(result.current.conflictState?.currentRevision).toBe(5);
    expect(await act(async () => result.current.save())).toMatchObject({ ok: false, reason: "conflict" });
    expect(server.wasCalled("PATCH", `/sections/${sectionA}`)).toBe(false);
  });

  it("重新获取租约且远端未变化时，仍保存失去租约前的草稿", async () => {
    const { result } = await openSection();
    await waitFor(() => expect(result.current.lease).not.toBeNull());
    await act(async () => result.current.setEditedMarkdown("恢复后继续保存"));
    server.mock("POST", `/sections/${sectionA}/lease/heartbeat`, { networkError: true });
    await act(async () => result.current.heartbeat());
    await act(async () => result.current.relock());
    expect(result.current.editedMarkdown).toBe("恢复后继续保存");
    expect(result.current.conflictState).toBeNull();
    server.mock("PATCH", `/sections/${sectionA}`, { body: makeSection(sectionA, 2, "恢复后继续保存") });
    expect(await act(async () => result.current.save())).toMatchObject({ ok: true, revision: 2 });
  });

  it("放弃并切换章节后，旧保存的迟到响应不覆盖新章节和 revision", async () => {
    vi.useFakeTimers();
    const { result } = await openSection();
    server.mock("PATCH", `/sections/${sectionA}`, { body: makeSection(sectionA, 2, "A 的修改"), delay: 100 });
    await act(async () => result.current.setEditedMarkdown("A 的修改"));
    let pending!: ReturnType<typeof result.current.save>;
    await act(async () => { pending = result.current.save(); });
    server.onGet(`/sections/${sectionB}`, makeSection(sectionB, 10, "B 的正文"));
    server.onGet(`/sections/${sectionB}/comments`, []);
    server.onPost(`/sections/${sectionB}/lease/acquire`, makeLease("lease-b", sectionB));
    await act(async () => result.current.switchSection(sectionB));
    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(await pending).toMatchObject({ ok: false, reason: "section_changed" });
    expect(result.current.selectedSectionId).toBe(sectionB);
    expect(result.current.editedMarkdown).toBe("B 的正文");
    expect(result.current.savedRevision).toBe(10);
  });
});
