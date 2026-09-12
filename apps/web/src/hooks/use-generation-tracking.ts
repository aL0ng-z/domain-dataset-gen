"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";

export type GenerationTrackStatus =
  | "queued"
  | "processing"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

export interface GenerationTrack {
  taskId: string;
  batchId: string;
  status: GenerationTrackStatus;
  canCancel?: boolean;
  errorCode?: string | null;
  errorMessage?: string | null;
  batch?: components["schemas"]["GenerationBatchResponse"];
}

interface Options {
  onTerminal?: (track: GenerationTrack) => void;
  onError?: () => void;
}

const TERMINAL = new Set<GenerationTrackStatus>(["completed", "failed", "cancelled"]);

function persistTrack(taskId: string, batchId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("task_id", taskId);
  url.searchParams.set("generation_batch_id", batchId);
  window.history.replaceState(null, "", url.toString());
}

/** Task 为执行真源、GenerationBatch 为业务汇总真源的共享跟踪器。 */
export function useGenerationTracking(projectId: string, options: Options = {}) {
  const [track, setTrack] = useState<GenerationTrack | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const trackVersionRef = useRef(0);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const taskId = params.get("task_id");
    const batchId = params.get("generation_batch_id");
    if (taskId && batchId) {
      setTrack((current) => current ?? { taskId, batchId, status: "queued" });
    }
  }, []);

  const startTrack = useCallback((taskId: string, batchId: string) => {
    trackVersionRef.current += 1;
    const next: GenerationTrack = { taskId, batchId, status: "queued" };
    setTrack(next);
    persistTrack(taskId, batchId);
  }, []);

  const clearTrack = useCallback(() => {
    trackVersionRef.current += 1;
    setTrack(null);
    setCancelling(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("task_id");
    url.searchParams.delete("generation_batch_id");
    window.history.replaceState(null, "", url.toString());
  }, []);

  const taskId = track?.taskId;
  const batchId = track?.batchId;
  const currentStatus = track?.status;

  useEffect(() => {
    if (!taskId || !batchId || !currentStatus || TERMINAL.has(currentStatus)) return;
    const controller = new AbortController();
    const trackVersion = trackVersionRef.current;
    const isCurrent = () => !controller.signal.aborted && trackVersionRef.current === trackVersion;
    let inFlight = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const task = await api.get("/projects/{pid}/tasks/{tid}", {
          params: { pid: projectId, tid: taskId },
          signal: controller.signal,
        });
        if (!isCurrent()) return;
        const status = task.status as GenerationTrackStatus;
        if (TERMINAL.has(status)) {
          const batch = await api.get("/projects/{pid}/generation-batches/{gbid}", {
            params: { pid: projectId, gbid: batchId },
            signal: controller.signal,
          });
          if (!isCurrent()) return;
          const terminal: GenerationTrack = {
            taskId,
            batchId,
            status,
            canCancel: task.can_cancel,
            errorCode: task.error_code,
            errorMessage: task.error_message,
            batch,
          };
          setTrack(terminal);
          optionsRef.current.onTerminal?.(terminal);
        } else {
          setTrack((current) => current?.taskId === taskId ? {
            ...current,
            status,
            canCancel: task.can_cancel,
            errorCode: task.error_code,
            errorMessage: task.error_message,
          } : current);
        }
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") return;
        if (isCurrent()) optionsRef.current.onError?.();
      } finally {
        inFlight = false;
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [projectId, taskId, batchId, currentStatus]);

  const cancelTask = useCallback(async () => {
    if (!track || cancelling || TERMINAL.has(track.status)) return;
    setCancelling(true);
    try {
      const result = await api.post(
        "/projects/{pid}/tasks/{tid}/cancel",
        undefined,
        { params: { pid: projectId, tid: track.taskId } },
      );
      setTrack((current) => current?.taskId === track.taskId ? {
        ...current,
        status: result.status as GenerationTrackStatus,
        canCancel: false,
      } : current);
    } finally {
      setCancelling(false);
    }
  }, [projectId, track, cancelling]);

  return { track, setTrack, startTrack, clearTrack, cancelTask, cancelling };
}
