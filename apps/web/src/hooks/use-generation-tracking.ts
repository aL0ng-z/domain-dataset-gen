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
    const next: GenerationTrack = { taskId, batchId, status: "queued" };
    setTrack(next);
    persistTrack(taskId, batchId);
  }, []);

  const taskId = track?.taskId;
  const batchId = track?.batchId;
  const currentStatus = track?.status;

  useEffect(() => {
    if (!taskId || !batchId || !currentStatus || TERMINAL.has(currentStatus)) return;
    const controller = new AbortController();
    let inFlight = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const task = await api.get("/projects/{pid}/tasks/{tid}", {
          params: { pid: projectId, tid: taskId },
          signal: controller.signal,
        });
        const status = task.status as GenerationTrackStatus;
        if (TERMINAL.has(status)) {
          const batch = await api.get("/projects/{pid}/generation-batches/{gbid}", {
            params: { pid: projectId, gbid: batchId },
            signal: controller.signal,
          });
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
        optionsRef.current.onError?.();
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
      setTrack((current) => current ? {
        ...current,
        status: result.status as GenerationTrackStatus,
        canCancel: false,
      } : current);
    } finally {
      setCancelling(false);
    }
  }, [projectId, track, cancelling]);

  return { track, setTrack, startTrack, cancelTask, cancelling };
}
