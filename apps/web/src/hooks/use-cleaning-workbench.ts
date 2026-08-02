"use client";

/**
 * 清洗工作台核心状态 hook（T05 前端合同）。
 *
 * 覆盖任务卡 §6：
 * - 每次选择 Section 生成请求代次并取消前一组详情/评论/acquire 请求；
 * - 只有“当前 section id + 当前代次”匹配的响应可更新状态；
 * - 编辑器仅在详情和租约均属于当前 Section 时可写；获取租约期间显示加载态；
 * - editedMarkdown 与服务端基线不同即为 dirty；切换章节/来源/路由时三选项对话框；
 * - 保存携带 content_revision + lease_id；409 保留本地文本并提供重新载入/复制；
 * - 心跳失败或标签页恢复发现 lease 过期：停止自动保存、只读、保留草稿；
 * - effect cleanup 只释放其捕获的 lease_id。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { components } from "@/lib/api/generated";
import { api, isAbortError, type ApiErrorException } from "@/lib/api";

export type Section = components["schemas"]["SectionResponse"];
export type SectionLease = components["schemas"]["SectionLeaseResponse"];
export type CleanedVersion = components["schemas"]["CleanedDocumentVersionResponse"];

export const LEASE_HEARTBEAT_MS = 30_000;
export const SAVE_DEBOUNCE_MS = 1200;

export interface SaveConflictInfo {
  currentRevision: number | null;
  leaseLost: boolean;
}

export function isBusinessError(e: unknown, code: string): boolean {
  if (!(e instanceof Error) || !("apiError" in e)) return false;
  const apiErr = (e as ApiErrorException).apiError;
  return apiErr.kind === "business" && apiErr.code === code;
}

export function useCleaningWorkbench() {
  // 选择状态
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null);
  const [selectedSection, setSelectedSection] = useState<Section | null>(null);
  const [editedMarkdown, setEditedMarkdown] = useState("");
  const [baselineRevision, setBaselineRevision] = useState<number>(0);
  const [lease, setLease] = useState<SectionLease | null>(null);
  const [acquiringLease, setAcquiringLease] = useState(false);
  const [leaseLost, setLeaseLost] = useState(false);
  const [comments, setComments] = useState<components["schemas"]["SectionCommentResponse"][]>([]);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // 代次与请求取消
  const requestGenRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const pendingLeaseRef = useRef<{ leaseId: string; sectionId: string } | null>(null);

  // 当前生效的（已提交到后端的）revision 基线：保存成功后推进。
  const [savedRevision, setSavedRevision] = useState(0);

  // 版本冲突：保留本地文本，暂停自动保存，等待用户重新载入或复制。
  const [conflictState, setConflictState] = useState<{ currentRevision: number } | null>(null);
  const autoSaveTimerRef = useRef<number | null>(null);
  const autoSavingRef = useRef(false);

  const isDirty = selectedSectionId !== null && editedMarkdown !== (selectedSection?.cleaned_markdown ?? selectedSection?.raw_markdown ?? "");

  // 取消当前 Section 的未决请求并推进代次。
  const bumpGeneration = useCallback(() => {
    requestGenRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    pendingLeaseRef.current = null;
  }, []);

  const loadSection = useCallback(
    async (sectionId: string) => {
      const gen = requestGenRef.current;
      const controller = new AbortController();
      abortRef.current = controller;

      setAcquiringLease(true);
      setLeaseLost(false);
      setSaving(false);
      setSubmitting(false);

      try {
        const [section, commentData] = await Promise.all([
          api.get("/sections/{sid}", { params: { sid: sectionId }, signal: controller.signal }),
          api.get("/sections/{sid}/comments", { params: { sid: sectionId }, signal: controller.signal }),
        ]);

        if (requestGenRef.current !== gen || abortRef.current !== controller) return;

        setSelectedSection(section);
        setEditedMarkdown(section.cleaned_markdown ?? section.raw_markdown ?? "");
        setBaselineRevision(section.content_revision ?? 0);
        setSavedRevision(section.content_revision ?? 0);
        setComments(commentData);
      } catch (e) {
        if (isAbortError(e)) return;
        if (requestGenRef.current === gen) toast.error("加载章节详情失败");
      }

      // 获取租约：acquire 请求也绑定当前代次；响应到达时若已切换则不应用。
      if (requestGenRef.current !== gen || abortRef.current !== controller) return;
      try {
        const leaseData = await api.post("/sections/{sid}/lease/acquire", undefined, {
          params: { sid: sectionId },
          signal: controller.signal,
        });
        if (requestGenRef.current !== gen || abortRef.current !== controller) return;
        setLease(leaseData);
        pendingLeaseRef.current = { leaseId: leaseData.id, sectionId };
      } catch (e) {
        if (isAbortError(e)) return;
        if (requestGenRef.current !== gen) return;
        if (isBusinessError(e, "SECTION_LEASE_HELD")) {
          setLeaseLost(true);
          setLease(null);
          toast.error("该章节已被其他用户锁定，当前只读");
        } else {
          toast.error("获取编辑租约失败");
        }
      } finally {
        if (requestGenRef.current === gen) setAcquiringLease(false);
      }
    },
    [],
  );

  // 切换 Section：先推进代次并取消旧请求，再加载新 Section。
  const switchSection = useCallback(
    (sectionId: string) => {
      bumpGeneration();
      setLease(null);
      pendingLeaseRef.current = null;
      setSelectedSectionId(sectionId);
      void loadSection(sectionId);
    },
    [bumpGeneration, loadSection],
  );

  // 心跳：只在当前 section + 当前 lease_id 匹配时续期；失败切只读。
  const heartbeat = useCallback(async () => {
    const leaseRef = pendingLeaseRef.current;
    if (!leaseRef) return;
    try {
      const renewed = await api.post("/sections/{sid}/lease/heartbeat", {
        lease_id: leaseRef.leaseId,
      }, {
        params: { sid: leaseRef.sectionId },
      });
      if (pendingLeaseRef.current?.leaseId === leaseRef.leaseId) {
        setLease(renewed);
      }
    } catch (e) {
      if (isAbortError(e)) return;
      if (pendingLeaseRef.current?.leaseId === leaseRef.leaseId) {
        pendingLeaseRef.current = null;
        setLeaseLost(true);
        setLease(null);
        toast.error("编辑租约已失效，已切换为只读");
      }
    }
  }, []);

  // 释放指定 lease（仅当仍归属当前 hook 时；effect cleanup 只释放捕获的 lease_id）。
  const releaseLease = useCallback(async (leaseId: string, sectionId: string) => {
    if (!leaseId) return;
    try {
      await api.post("/sections/{sid}/lease/release", { lease_id: leaseId }, {
        params: { sid: sectionId },
      });
    } catch {
      // 释放失败（网络/已过期）静默：数据库过期保护兜底。
    }
  }, []);

  // 保存：携带当前 content_revision + lease_id；409 保留本地文本。
  const save = useCallback(async (): Promise<SaveConflictInfo> => {
    if (!selectedSectionId || !lease) return { currentRevision: null, leaseLost: false };
    const sectionId = selectedSectionId;
    const leaseId = lease.id;
    const expectedRevision = savedRevision;
    setSaving(true);
    try {
      const updated = await api.patch("/sections/{sid}", {
        cleaned_markdown: editedMarkdown,
        expected_revision: expectedRevision,
        lease_id: leaseId,
      }, {
        params: { sid: sectionId },
      });
      // 保存成功后原子更新基线/revision/dirty。
      setSelectedSection(updated);
      setSavedRevision(updated.content_revision ?? expectedRevision + 1);
      setBaselineRevision(updated.content_revision ?? expectedRevision + 1);
      setConflictState(null);
      return { currentRevision: null, leaseLost: false };
    } catch (e) {
      if (isAbortError(e)) return { currentRevision: null, leaseLost: false };
      if (isBusinessError(e, "SECTION_VERSION_CONFLICT")) {
        const apiErr = (e as ApiErrorException).apiError;
        const context = apiErr.kind === "business" ? (apiErr.context as { current_revision?: number } | null) : null;
        const currentRevision = context?.current_revision ?? null;
        if (currentRevision !== null) {
          setConflictState({ currentRevision });
        }
        toast.error("内容已被他人更新，请重新载入或复制本地内容");
        return { currentRevision, leaseLost: false };
      }
      if (isBusinessError(e, "SECTION_LEASE_LOST")) {
        setLeaseLost(true);
        setLease(null);
        toast.error("编辑租约已失效，已切换为只读");
        return { currentRevision: null, leaseLost: true };
      }
      toast.error("保存失败");
      return { currentRevision: null, leaseLost: false };
    } finally {
      setSaving(false);
    }
  }, [selectedSectionId, lease, editedMarkdown, savedRevision]);

  // 提交审核：携带 revision + lease_id；成功后释放 lease。
  const submit = useCallback(async (): Promise<boolean> => {
    if (!selectedSectionId || !lease) return false;
    const sectionId = selectedSectionId;
    const leaseId = lease.id;
    setSubmitting(true);
    try {
      await api.post("/sections/{sid}/submit", {
        expected_revision: savedRevision,
        lease_id: leaseId,
      }, {
        params: { sid: sectionId },
      });
      pendingLeaseRef.current = null;
      setLease(null);
      return true;
    } catch (e) {
      if (isAbortError(e)) return false;
      if (isBusinessError(e, "SECTION_LEASE_LOST")) {
        setLeaseLost(true);
        setLease(null);
        toast.error("编辑租约已失效，请重新获取");
      } else if (isBusinessError(e, "SECTION_VERSION_CONFLICT")) {
        toast.error("内容版本已变化，请重新载入后再提交");
      } else {
        toast.error("提交审核失败");
      }
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [selectedSectionId, lease, savedRevision]);

  // 心跳定时器：随 section/lease 变化重建。
  useEffect(() => {
    if (!pendingLeaseRef.current) return;
    const timer = window.setInterval(() => {
      void heartbeat();
    }, LEASE_HEARTBEAT_MS);
    return () => window.clearInterval(timer);
  }, [selectedSectionId, heartbeat]);

  // 卸载/切换时：只释放本 hook 捕获的 lease（capture 语义）。
  useEffect(() => {
    const currentLease = pendingLeaseRef.current;
    return () => {
      if (currentLease) {
        void releaseLease(currentLease.leaseId, currentLease.sectionId);
      }
    };
  }, [selectedSectionId, releaseLease]);

  // 标签页恢复/可见性恢复时检查租约是否过期。
  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden && pendingLeaseRef.current) {
        void heartbeat();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [heartbeat]);

  const resetForCleanup = useCallback(() => {
    bumpGeneration();
    pendingLeaseRef.current = null;
    setSelectedSectionId(null);
    setSelectedSection(null);
    setEditedMarkdown("");
    setComments([]);
    setLease(null);
    setLeaseLost(false);
    setSavedRevision(0);
    setBaselineRevision(0);
    setConflictState(null);
  }, [bumpGeneration]);

  // 版本冲突后的重新载入：丢弃本地文本，重新拉取服务端最新内容与 revision。
  const reloadFromServer = useCallback(async () => {
    if (!selectedSectionId) return;
    const sectionId = selectedSectionId;
    try {
      const fresh = await api.get("/sections/{sid}", { params: { sid: sectionId } });
      setSelectedSection(fresh);
      setEditedMarkdown(fresh.cleaned_markdown ?? fresh.raw_markdown ?? "");
      setBaselineRevision(fresh.content_revision ?? 0);
      setSavedRevision(fresh.content_revision ?? 0);
      setConflictState(null);
    } catch {
      toast.error("重新载入失败");
    }
  }, [selectedSectionId]);

  // 冲突后重新获取租约：必须重新拉取 revision，不能直接提交旧基线。
  const relock = useCallback(async () => {
    if (!selectedSectionId) return;
    const sectionId = selectedSectionId;
    setLeaseLost(false);
    setAcquiringLease(true);
    try {
      const leaseData = await api.post("/sections/{sid}/lease/acquire", undefined, {
        params: { sid: sectionId },
      });
      setLease(leaseData);
      pendingLeaseRef.current = { leaseId: leaseData.id, sectionId };
      // 重新获取 lease 后重新拉取 revision（不能直接提交旧基线）。
      await reloadFromServer();
    } catch (e) {
      if (isBusinessError(e, "SECTION_LEASE_HELD")) {
        setLeaseLost(true);
        toast.error("该章节仍被其他用户锁定");
      } else {
        toast.error("重新获取租约失败");
      }
    } finally {
      setAcquiringLease(false);
    }
  }, [selectedSectionId, reloadFromServer]);

  // 自动保存：dirty 且未冲突且持租约时，防抖保存。冲突/租约丢失即暂停。
  useEffect(() => {
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    if (!isDirty || conflictState || !lease || leaseLost || acquiringLease) return;
    autoSaveTimerRef.current = window.setTimeout(() => {
      if (autoSavingRef.current) return;
      autoSavingRef.current = true;
      void save().finally(() => {
        autoSavingRef.current = false;
      });
    }, SAVE_DEBOUNCE_MS);
    return () => {
      if (autoSaveTimerRef.current) {
        window.clearTimeout(autoSaveTimerRef.current);
        autoSaveTimerRef.current = null;
      }
    };
  }, [editedMarkdown, isDirty, conflictState, lease, leaseLost, acquiringLease, save]);

  // beforeunload：有未保存修改时提示。
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (isDirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [isDirty]);

  return {
    selectedSectionId,
    selectedSection,
    editedMarkdown,
    setEditedMarkdown,
    baselineRevision,
    savedRevision,
    lease,
    leaseLost,
    acquiringLease,
    comments,
    setComments,
    saving,
    submitting,
    isDirty,
    conflictState,
    switchSection,
    setSelectedSectionId,
    loadSection,
    heartbeat,
    releaseLease,
    save,
    submit,
    reloadFromServer,
    relock,
    resetForCleanup,
    requestGeneration: requestGenRef,
    abortRef,
  };
}
