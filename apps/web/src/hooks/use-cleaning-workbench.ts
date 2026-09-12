"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { components } from "@/lib/api/generated";
import { api, isAbortError, type ApiErrorException } from "@/lib/api";

export type Section = components["schemas"]["SectionResponse"];
export type SectionLease = components["schemas"]["SectionLeaseResponse"];
export type CleanedVersion = components["schemas"]["CleanedDocumentVersionResponse"];

export const LEASE_HEARTBEAT_MS = 30_000;
export const SAVE_DEBOUNCE_MS = 1200;

export type SaveResult =
  | { ok: true; revision: number }
  | { ok: false; reason: "conflict"; currentRevision: number | null }
  | { ok: false; reason: "lease_lost" | "request_failed" | "section_changed" };

type EditSession = {
  sectionId: string;
  section: Section | null;
  text: string;
  revision: number;
  conflict: { currentRevision: number | null } | null;
};

export function isBusinessError(e: unknown, code: string): boolean {
  if (!(e instanceof Error) || !("apiError" in e)) return false;
  const apiErr = (e as ApiErrorException).apiError;
  return apiErr.kind === "business" && apiErr.code === code;
}

function markdown(section: Section | null): string {
  return section?.cleaned_markdown ?? section?.raw_markdown ?? "";
}

/** 单个章节会话拥有正文基线、租约和保存队列；异步响应只能更新发起它的会话。 */
export function useCleaningWorkbench() {
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null);
  const [selectedSection, setSelectedSection] = useState<Section | null>(null);
  const [editedMarkdown, updateEditedMarkdown] = useState("");
  const [savedRevision, setSavedRevision] = useState(0);
  const [lease, setLease] = useState<SectionLease | null>(null);
  const [acquiringLease, setAcquiringLease] = useState(false);
  const [leaseLost, setLeaseLost] = useState(false);
  const [comments, setComments] = useState<components["schemas"]["SectionCommentResponse"][]>([]);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [conflictState, setConflictState] = useState<{ currentRevision: number | null } | null>(null);
  const requestGenRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<EditSession | null>(null);
  const pendingLeaseRef = useRef<{ leaseId: string; sectionId: string } | null>(null);
  const saveFlightRef = useRef<{ session: EditSession; promise: Promise<SaveResult> } | null>(null);
  const submittingRef = useRef(false);
  const autoSaveTimerRef = useRef<number | null>(null);

  const isDirty = selectedSectionId !== null && editedMarkdown !== markdown(selectedSection);

  const cancelAutoSave = useCallback(() => {
    if (autoSaveTimerRef.current !== null) window.clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = null;
  }, []);

  // 同步更新会话，避免编辑与按钮点击在同一轮事件中读取到旧正文。
  const setEditedMarkdown = useCallback((text: string) => {
    if (submittingRef.current) return;
    if (sessionRef.current) sessionRef.current.text = text;
    updateEditedMarkdown(text);
  }, []);

  const releaseLease = useCallback(async (leaseId: string, sectionId: string) => {
    try {
      await api.post("/sections/{sid}/lease/release", { lease_id: leaseId }, { params: { sid: sectionId } });
    } catch {
      // 释放失败由数据库的到期时间兜底。
    }
  }, []);

  const clearSession = useCallback(() => {
    cancelAutoSave();
    requestGenRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    const previousLease = pendingLeaseRef.current;
    pendingLeaseRef.current = null;
    sessionRef.current = null;
    saveFlightRef.current = null;
    submittingRef.current = false;
    setLease(null);
    setConflictState(null);
    setSaving(false);
    setSubmitting(false);
    if (previousLease) void releaseLease(previousLease.leaseId, previousLease.sectionId);
  }, [cancelAutoSave, releaseLease]);

  const applySection = useCallback((session: EditSession, section: Section, replaceDraft: boolean) => {
    session.section = section;
    session.revision = section.content_revision;
    setSelectedSection(section);
    setSavedRevision(section.content_revision);
    if (replaceDraft) {
      session.text = markdown(section);
      updateEditedMarkdown(session.text);
    }
  }, []);

  const loseLease = useCallback(() => {
    cancelAutoSave();
    pendingLeaseRef.current = null;
    setLeaseLost(true);
    setLease(null);
  }, [cancelAutoSave]);

  const loadSection = useCallback(async (sectionId: string) => {
    clearSession();
    const session: EditSession = { sectionId, section: null, text: "", revision: 0, conflict: null };
    sessionRef.current = session;
    const controller = new AbortController();
    abortRef.current = controller;
    setSelectedSectionId(sectionId);
    setSelectedSection(null);
    updateEditedMarkdown("");
    setComments([]);
    setAcquiringLease(true);
    setLeaseLost(false);
    try {
      const [section, commentData] = await Promise.all([
        api.get("/sections/{sid}", { params: { sid: sectionId }, signal: controller.signal }),
        api.get("/sections/{sid}/comments", { params: { sid: sectionId }, signal: controller.signal }),
      ]);
      if (sessionRef.current !== session) return;
      applySection(session, section, true);
      setComments(commentData);
      const leaseData = await api.post("/sections/{sid}/lease/acquire", undefined, {
        params: { sid: sectionId }, signal: controller.signal,
      });
      if (sessionRef.current !== session) return;
      pendingLeaseRef.current = { leaseId: leaseData.id, sectionId };
      setLease(leaseData);
    } catch (e) {
      if (isAbortError(e) || sessionRef.current !== session) return;
      if (isBusinessError(e, "SECTION_LEASE_HELD")) {
        loseLease();
        toast.error("该章节已被其他用户锁定，当前只读");
      } else {
        if (session.section) loseLease();
        toast.error(session.section ? "获取编辑租约失败" : "加载章节详情失败");
      }
    } finally {
      if (sessionRef.current === session) setAcquiringLease(false);
    }
  }, [applySection, clearSession, loseLease]);

  const switchSection = useCallback((sectionId: string) => {
    if (submittingRef.current) return;
    void loadSection(sectionId);
  }, [loadSection]);

  const heartbeat = useCallback(async () => {
    const held = pendingLeaseRef.current;
    if (!held) return;
    try {
      const renewed = await api.post("/sections/{sid}/lease/heartbeat", { lease_id: held.leaseId }, {
        params: { sid: held.sectionId },
      });
      if (pendingLeaseRef.current === held) setLease(renewed);
    } catch (e) {
      if (!isAbortError(e) && pendingLeaseRef.current === held) {
        loseLease();
        toast.error("编辑租约已失效，已切换为只读");
      }
    }
  }, [loseLease]);

  const recordConflict = useCallback((session: EditSession, e: unknown) => {
    const apiErr = (e as ApiErrorException).apiError;
    const context = apiErr.kind === "business" ? apiErr.context as { current_revision?: number } | null : null;
    session.conflict = { currentRevision: context?.current_revision ?? null };
    setConflictState(session.conflict);
    cancelAutoSave();
    return session.conflict.currentRevision;
  }, [cancelAutoSave]);

  // 所有调用者复用同一条保存队列。每次响应只推进已发送文本的基线；新增输入继续排队。
  const save = useCallback((): Promise<SaveResult> => {
    cancelAutoSave();
    const session = sessionRef.current;
    if (!session?.section) return Promise.resolve({ ok: false, reason: "section_changed" });
    if (saveFlightRef.current?.session === session) return saveFlightRef.current.promise;
    const held = pendingLeaseRef.current;
    if (!held || held.sectionId !== session.sectionId) return Promise.resolve({ ok: false, reason: "lease_lost" });
    const run = async (): Promise<SaveResult> => {
      setSaving(true);
      try {
        while (true) {
          if (sessionRef.current !== session) return { ok: false, reason: "section_changed" };
          if (session.conflict) return { ok: false, reason: "conflict", ...session.conflict };
          if (pendingLeaseRef.current !== held) return { ok: false, reason: "lease_lost" };
          if (session.text === markdown(session.section)) return { ok: true, revision: session.revision };
          const text = session.text;
          const updated = await api.patch("/sections/{sid}", {
            cleaned_markdown: text, expected_revision: session.revision, lease_id: held.leaseId,
          }, { params: { sid: session.sectionId } });
          if (sessionRef.current !== session) return { ok: false, reason: "section_changed" };
          applySection(session, updated, false);
        }
      } catch (e) {
        if (sessionRef.current !== session) return { ok: false, reason: "section_changed" };
        if (isBusinessError(e, "SECTION_VERSION_CONFLICT")) {
          const currentRevision = recordConflict(session, e);
          toast.error("内容已被他人更新，请重新载入或复制本地内容");
          return { ok: false, reason: "conflict", currentRevision };
        }
        if (isBusinessError(e, "SECTION_LEASE_LOST")) {
          loseLease();
          toast.error("编辑租约已失效，已切换为只读");
          return { ok: false, reason: "lease_lost" };
        }
        if (!isAbortError(e)) toast.error("保存失败，草稿已保留");
        return { ok: false, reason: "request_failed" };
      }
    };
    const promise = run().finally(() => {
      if (saveFlightRef.current?.session === session) saveFlightRef.current = null;
      if (sessionRef.current === session) setSaving(false);
    });
    saveFlightRef.current = { session, promise };
    return promise;
  }, [applySection, cancelAutoSave, loseLease, recordConflict]);

  const submit = useCallback(async (): Promise<boolean> => {
    if (submittingRef.current) return false;
    const session = sessionRef.current;
    const held = pendingLeaseRef.current;
    if (!session || !held) return false;
    submittingRef.current = true;
    setSubmitting(true);
    cancelAutoSave();
    try {
      const saved = await save();
      if (!saved.ok || sessionRef.current !== session || pendingLeaseRef.current !== held) return false;
      const updated = await api.post("/sections/{sid}/submit", {
        expected_revision: saved.revision, lease_id: held.leaseId,
      }, { params: { sid: session.sectionId } });
      if (sessionRef.current !== session) return false;
      applySection(session, updated, false);
      pendingLeaseRef.current = null;
      setLease(null);
      return true;
    } catch (e) {
      if (sessionRef.current !== session || isAbortError(e)) return false;
      if (isBusinessError(e, "SECTION_LEASE_LOST")) {
        loseLease();
        toast.error("编辑租约已失效，请重新获取");
      } else if (isBusinessError(e, "SECTION_VERSION_CONFLICT")) {
        recordConflict(session, e);
        toast.error("内容版本已变化，请重新载入后再提交");
      } else {
        toast.error("提交审核失败");
      }
      return false;
    } finally {
      if (sessionRef.current === session) {
        submittingRef.current = false;
        setSubmitting(false);
      }
    }
  }, [applySection, cancelAutoSave, loseLease, recordConflict, save]);

  // 必须依赖 lease.id：首次渲染尚未取得的异步租约到达后才创建定时器。
  const leaseId = lease?.id;
  const leaseSectionId = lease?.section_id;
  useEffect(() => {
    if (!leaseId || leaseSectionId !== selectedSectionId) return;
    const timer = window.setInterval(() => void heartbeat(), LEASE_HEARTBEAT_MS);
    return () => window.clearInterval(timer);
  }, [selectedSectionId, leaseId, leaseSectionId, heartbeat]);

  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden) void heartbeat();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [heartbeat]);

  useEffect(() => () => {
    requestGenRef.current += 1;
    abortRef.current?.abort();
    sessionRef.current = null;
    cancelAutoSave();
    const held = pendingLeaseRef.current;
    pendingLeaseRef.current = null;
    if (held) void releaseLease(held.leaseId, held.sectionId);
  }, [cancelAutoSave, releaseLease]);

  const resetForCleanup = useCallback(() => {
    if (submittingRef.current) return;
    clearSession();
    setSelectedSectionId(null);
    setSelectedSection(null);
    updateEditedMarkdown("");
    setComments([]);
    setLeaseLost(false);
    setAcquiringLease(false);
    setSavedRevision(0);
  }, [clearSession]);

  // 只有用户显式选择重新载入时才丢弃草稿。
  const reloadFromServer = useCallback(async () => {
    const session = sessionRef.current;
    if (!session || submittingRef.current || saveFlightRef.current) return;
    cancelAutoSave();
    try {
      const fresh = await api.get("/sections/{sid}", { params: { sid: session.sectionId } });
      if (sessionRef.current !== session) return;
      applySection(session, fresh, true);
      session.conflict = null;
      setConflictState(null);
    } catch {
      if (sessionRef.current === session) toast.error("重新载入失败");
    }
  }, [applySection, cancelAutoSave]);

  const relock = useCallback(async () => {
    const session = sessionRef.current;
    if (!session || submittingRef.current || saveFlightRef.current) return;
    cancelAutoSave();
    setAcquiringLease(true);
    try {
      const leaseData = await api.post("/sections/{sid}/lease/acquire", undefined, {
        params: { sid: session.sectionId },
      });
      if (sessionRef.current !== session) return;
      // 先取得租约再核对远端基线；新租约不能让旧草稿悄悄覆盖远端更新。
      pendingLeaseRef.current = { leaseId: leaseData.id, sectionId: session.sectionId };
      const fresh = await api.get("/sections/{sid}", { params: { sid: session.sectionId } });
      if (sessionRef.current !== session) return;
      if (fresh.content_revision !== session.revision) {
        session.conflict = { currentRevision: fresh.content_revision };
        setConflictState(session.conflict);
        toast.error("服务端内容已更新，草稿已保留，请复制草稿或重新载入");
      }
      setLease(leaseData);
      setLeaseLost(false);
    } catch (e) {
      if (sessionRef.current !== session) return;
      const held = pendingLeaseRef.current;
      if (held) void releaseLease(held.leaseId, held.sectionId);
      loseLease();
      toast.error(isBusinessError(e, "SECTION_LEASE_HELD") ? "该章节仍被其他用户锁定" : "重新获取租约失败");
    } finally {
      if (sessionRef.current === session) setAcquiringLease(false);
    }
  }, [cancelAutoSave, loseLease, releaseLease]);

  useEffect(() => {
    cancelAutoSave();
    if (!isDirty || conflictState || !lease || leaseLost || acquiringLease || submitting || saving) return;
    autoSaveTimerRef.current = window.setTimeout(() => void save(), SAVE_DEBOUNCE_MS);
    return cancelAutoSave;
  }, [editedMarkdown, isDirty, conflictState, lease, leaseLost, acquiringLease, submitting, saving, save, cancelAutoSave]);

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
    selectedSectionId, selectedSection, editedMarkdown, setEditedMarkdown,
    baselineRevision: savedRevision, savedRevision, lease, leaseLost, acquiringLease,
    comments, setComments, saving, submitting, isDirty, conflictState,
    switchSection, setSelectedSectionId, loadSection, heartbeat, releaseLease,
    save, submit, reloadFromServer, relock, resetForCleanup,
    requestGeneration: requestGenRef, abortRef,
  };
}
