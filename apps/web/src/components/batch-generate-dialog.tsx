"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, ApiErrorException } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { useGenerationTracking } from "@/hooks/use-generation-tracking";
import { Loader2Icon, RotateCwIcon, XCircleIcon } from "lucide-react";

type Template = components["schemas"]["PromptTemplateResponse"];
type ModelConfig = components["schemas"]["ModelConfigResponse"];
type ChunkItem = components["schemas"]["ChunkResponse"];

/** summary_json 的稳定结构（任务卡 §4）。 */
interface BatchSummary {
  succeeded: number;
  failed: number;
  cancelled: number;
  candidate_ids: string[];
  failures: Array<{ chunk_id: string | null; code: string; message: string }>;
}

/** 从 JSONB 类型安全读取 summary_json。 */
function asSummary(v: unknown): BatchSummary | undefined {
  if (!v || typeof v !== "object") return undefined;
  const o = v as Record<string, unknown>;
  return {
    succeeded: typeof o.succeeded === "number" ? o.succeeded : 0,
    failed: typeof o.failed === "number" ? o.failed : 0,
    cancelled: typeof o.cancelled === "number" ? o.cancelled : 0,
    candidate_ids: Array.isArray(o.candidate_ids) ? (o.candidate_ids as string[]) : [],
    failures: Array.isArray(o.failures) ? (o.failures as BatchSummary["failures"]) : [],
  };
}

interface Props {
  projectId: string;
  docId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 生成成功后回调（刷新父页面） */
  onGenerated?: () => void;
}

/**
 * 文档详情页"批量生成"对话框（任务卡 §6）：
 * - 选择模板 + 模型 + "全部 ready / 已选 chunks"。
 * - 202 接收后展示 queued/processing，不谎报"生成完成"。
 * - 重复提交期间禁用按钮。
 * - 部分失败展示成功/失败/取消计数与可定位失败列表。
 */
export function BatchGenerateDialog({ projectId, docId, open, onOpenChange, onGenerated }: Props) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [mode, setMode] = useState<"all" | "selected">("all");
  const [selectedChunkIds, setSelectedChunkIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const submissionKeyRef = useRef<{ intent: string; key: string } | null>(null);
  const retryKeyRef = useRef<{ batchId: string; key: string } | null>(null);
  const { track, startTrack, cancelTask, cancelling } = useGenerationTracking(projectId, {
    onTerminal: () => onGenerated?.(),
    onError: () => toast.error("跟踪生成状态失败"),
  });

  // 打开时加载配置与 ready chunks。
  useEffect(() => {
    if (!open) return;
    Promise.all([
      api
        .get("/projects/{pid}/prompt-templates/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 100 },
        })
        .catch(() => ({ items: [] as Template[] })),
      api
        .get("/projects/{pid}/model-configs/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 100 },
        })
        .catch(() => ({ items: [] as ModelConfig[] })),
      api
        .get("/projects/{pid}/documents/{did}/chunks", {
          params: { pid: projectId, did: docId },
          query: { page: 1, page_size: 200, status: "ready" },
        })
        .catch(() => ({ items: [] as ChunkItem[], total: 0, page: 1, page_size: 200 })),
    ]).then(([tplData, modelData, chunkData]) => {
      setTemplates(tplData.items);
      setModels(modelData.items);
      setChunks(chunkData.items);
      const tpl = tplData.items.find((t) => t.is_default) || tplData.items[0];
      if (tpl) setSelectedTemplate(tpl.id);
      const mdl = modelData.items.find((m) => m.is_default) || modelData.items[0];
      if (mdl) setSelectedModel(mdl.id);
    });
  }, [open, projectId, docId]);

  const readyChunks = useMemo(() => chunks, [chunks]);

  const canSubmit = !!selectedTemplate && !!selectedModel && (mode === "all" || selectedChunkIds.length > 0);

  const toggleChunk = (id: string) => {
    setSelectedChunkIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const handleSubmit = async () => {
    if (!canSubmit) {
      toast.error("请选择模板、模型及要生成的 Chunk");
      return;
    }
    setSubmitting(true);
    try {
      const intent = JSON.stringify([
        docId,
        selectedTemplate,
        selectedModel,
        mode,
        mode === "selected" ? [...selectedChunkIds].sort() : null,
      ]);
      const key = submissionKeyRef.current?.intent === intent
        ? submissionKeyRef.current.key
        : `gen-submit-${crypto.randomUUID()}`;
      submissionKeyRef.current = { intent, key };
      const result = await api.post(
        "/projects/{pid}/documents/{did}/generate-batch",
        {
          prompt_template_id: selectedTemplate,
          model_config_id: selectedModel,
          selected_chunk_ids: mode === "selected" ? selectedChunkIds : undefined,
        },
        {
          params: { pid: projectId, did: docId },
          headers: { "Idempotency-Key": key },
        },
      );
      submissionKeyRef.current = null;
      startTrack(result.task_id, result.generation_batch_id);
      toast.success("批量生成任务已提交");
    } catch (e) {
      if (e instanceof ApiErrorException) {
        toast.error(e.apiError.message);
      } else {
        toast.error("提交失败");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async () => {
    if (!track?.batch || track.batch.provenance_status !== "verified") return;
    setSubmitting(true);
    try {
      const key = retryKeyRef.current?.batchId === track.batchId
        ? retryKeyRef.current.key
        : `gen-retry-${crypto.randomUUID()}`;
      retryKeyRef.current = { batchId: track.batchId, key };
      const result = await api.post(
        "/projects/{pid}/generation-batches/{gbid}/retry",
        undefined,
        {
          params: { pid: projectId, gbid: track.batchId },
          headers: { "Idempotency-Key": key },
        },
      );
      retryKeyRef.current = null;
      startTrack(result.task_id, result.generation_batch_id);
      toast.success("已创建重试任务");
    } catch (e) {
      if (e instanceof ApiErrorException) {
        toast.error(e.apiError.message);
      } else {
        toast.error("重试失败");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>批量生成</DialogTitle>
          <DialogDescription>
            选择模板、模型及 Chunk 范围发起批量生成；202 接收后展示执行状态。
          </DialogDescription>
        </DialogHeader>

        {track ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">生成状态</span>
              <StatusBadge status={track.status} />
              {["queued", "processing"].includes(track.status) && (
                <Loader2Icon className="size-3 animate-spin" />
              )}
              {track.canCancel && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={cancelling}
                  onClick={() => void cancelTask()}
                >
                  <XCircleIcon className="size-3" />
                  {cancelling ? "取消中" : "取消任务"}
                </Button>
              )}
            </div>
            {(track.errorCode || track.errorMessage) && (
              <div className="text-xs text-destructive">
                {track.errorCode ? `${track.errorCode}: ` : ""}{track.errorMessage}
              </div>
            )}
            <div className="text-xs text-muted-foreground space-y-1">
              <div>任务 ID: <span className="font-mono">{track.taskId}</span></div>
              <div>批次 ID: <span className="font-mono">{track.batchId}</span></div>
            </div>
            {track.batch && (
              <div className="text-xs space-y-1 border-t pt-2">
                <div>
                  进度：{track.batch.completed_chunks}/{track.batch.total_chunks} chunks
                </div>
                {(() => {
                  const summary = asSummary(track.batch?.summary_json);
                  return summary ? (
                    <div>
                      成功 {summary.succeeded} / 失败 {summary.failed} / 取消 {summary.cancelled}
                    </div>
                  ) : null;
                })()}
                {(() => {
                  const summary = asSummary(track.batch?.summary_json);
                  return summary && summary.failed > 0 ? (
                    <ul className="text-destructive space-y-1 mt-1">
                      {summary.failures.map((f, i) => (
                        <li key={i}>
                          chunk {String(f.chunk_id).slice(0, 8)} — {f.code}: {f.message}
                        </li>
                      ))}
                    </ul>
                  ) : null;
                })()}
                {track.batch.provenance_status !== "verified" && (
                  <div className="text-amber-600">
                    provenance: {track.batch.provenance_status}
                    {track.batch.provenance_error_code
                      ? ` (${track.batch.provenance_error_code})`
                      : ""}
                    ，禁止重试和作为可信导出来源。
                  </div>
                )}
              </div>
            )}
            <DialogFooter>
              {track.batch &&
                ["failed", "cancelled"].includes(track.batch.status) &&
                track.batch.provenance_status === "verified" && (
                  <Button
                    variant="outline"
                    disabled={submitting}
                    onClick={() => void handleRetry()}
                  >
                    <RotateCwIcon className="size-3" />
                    重试失败项
                  </Button>
                )}
              <Button onClick={() => onOpenChange(false)}>关闭</Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium block mb-1">模板</label>
                  <select
                    className="w-full rounded border px-2 py-1.5 text-sm bg-transparent"
                    value={selectedTemplate}
                    onChange={(e) => setSelectedTemplate(e.target.value)}
                  >
                    {templates.length === 0 && <option value="">暂无模板</option>}
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name} ({t.task_type})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium block mb-1">模型</label>
                  <select
                    className="w-full rounded border px-2 py-1.5 text-sm bg-transparent"
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                  >
                    {models.length === 0 && <option value="">暂无模型</option>}
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name} ({m.model_name})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="flex items-center gap-3 text-sm">
                <label className="inline-flex items-center gap-1">
                  <input
                    type="radio"
                    name="mode"
                    checked={mode === "all"}
                    onChange={() => setMode("all")}
                  />
                  全部 ready chunks
                </label>
                <label className="inline-flex items-center gap-1">
                  <input
                    type="radio"
                    name="mode"
                    checked={mode === "selected"}
                    onChange={() => setMode("selected")}
                  />
                  已选 chunks
                </label>
              </div>

              {mode === "selected" && (
                <div className="border rounded max-h-48 overflow-auto">
                  {readyChunks.map((c) => (
                    <label
                      key={c.id}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted/50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedChunkIds.includes(c.id)}
                        onChange={() => toggleChunk(c.id)}
                      />
                      #{c.ordinal + 1} — {c.heading_path || "无标题"}
                    </label>
                  ))}
                  {readyChunks.length === 0 && (
                    <div className="px-3 py-2 text-muted-foreground">没有 ready chunks</div>
                  )}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
                取消
              </Button>
              <Button onClick={handleSubmit} disabled={submitting || !canSubmit}>
                {submitting && <Loader2Icon className="size-3 animate-spin" />}
                发起批量生成
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
