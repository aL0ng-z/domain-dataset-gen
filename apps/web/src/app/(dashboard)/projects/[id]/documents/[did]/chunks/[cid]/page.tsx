"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { api, ApiErrorException } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { useGenerationTracking } from "@/hooks/use-generation-tracking";
import { ArrowLeftIcon, PlayIcon, Loader2Icon, RotateCwIcon, XCircleIcon } from "lucide-react";

type ChunkDetail = components["schemas"]["ChunkResponse"];
type Template = components["schemas"]["PromptTemplateResponse"];
type ModelConfig = components["schemas"]["ModelConfigResponse"];
type CandidateItem = components["schemas"]["CandidateResponse"];

/** summary_json 的稳定结构（任务卡 §4：{succeeded, failed, cancelled, candidate_ids, failures}）。 */
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
    failures: Array.isArray(o.failures)
      ? (o.failures as BatchSummary["failures"])
      : [],
  };
}

export default function ChunkDetailPage() {
  const params = useParams<{ id: string; did: string; cid: string }>();
  const projectId = params.id;
  const docId = params.did;
  const chunkId = params.cid;

  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>("");
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [candidates, setCandidates] = useState<CandidateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const submissionKeyRef = useRef<{ intent: string; key: string } | null>(null);
  const retryKeyRef = useRef<{ batchId: string; key: string } | null>(null);
  const { track, startTrack, cancelTask, cancelling } = useGenerationTracking(projectId, {
    onError: () => toast.error("跟踪生成状态失败"),
  });

  useEffect(() => {
    Promise.all([
      api.get("/chunks/{cid}", { params: { cid: chunkId } }),
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
    ])
      .then(([chunkData, templateData, modelData]) => {
        setChunk(chunkData);
        setTemplates(templateData.items);
        setModels(modelData.items);
        if (templateData.items.length > 0 && !selectedTemplate) {
          setSelectedTemplate(templateData.items[0].id);
        }
        if (modelData.items.length > 0 && !selectedModel) {
          const def = modelData.items.find((m) => m.is_default) || modelData.items[0];
          setSelectedModel(def.id);
        }
      })
      .catch(() => toast.error("加载分块详情失败"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, docId, chunkId]);

  const handleGenerate = useCallback(async () => {
    if (!selectedTemplate || !selectedModel) {
      toast.error("请同时选择模板与模型");
      return;
    }
    setSubmitting(true);
    try {
      const intent = JSON.stringify([chunkId, selectedTemplate, selectedModel]);
      const key = submissionKeyRef.current?.intent === intent
        ? submissionKeyRef.current.key
        : `gen-submit-${crypto.randomUUID()}`;
      submissionKeyRef.current = { intent, key };
      const result = await api.post(
        "/chunks/{cid}/generate",
        {
          prompt_template_id: selectedTemplate,
          model_config_id: selectedModel,
        },
        {
          params: { cid: chunkId },
          headers: { "Idempotency-Key": key },
        },
      );
      submissionKeyRef.current = null;
      startTrack(result.task_id, result.generation_batch_id);
      toast.success("生成任务已提交，等待执行");
    } catch (e) {
      if (e instanceof ApiErrorException) {
        toast.error(e.apiError.message);
      } else {
        toast.error("生成失败");
      }
    } finally {
      setSubmitting(false);
    }
  }, [chunkId, selectedTemplate, selectedModel, startTrack]);

  useEffect(() => {
    if (!track?.batch) return;
    api.get("/chunks/{cid}/candidates", {
      params: { cid: chunkId },
      query: { page: 1, page_size: 20 },
    }).then((page) => setCandidates(page.items)).catch(() => {});
  }, [chunkId, track?.batch]);

  // retry：跟随新 Task/Batch。
  const handleRetry = useCallback(async () => {
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
  }, [projectId, track, startTrack]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">加载中...</div>
      </div>
    );
  }

  if (!chunk) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">分块不存在</div>
      </div>
    );
  }

  const showBusy =
    track && ["queued", "processing", "cancelling"].includes(track.status);

  return (
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/documents/${docId}/chunks`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回分块列表
        </Link>
        <h1 className="text-2xl font-semibold">分块 #{chunk.ordinal + 1}</h1>
        <div className="flex items-center gap-2 mt-1">
          <StatusBadge status={chunk.status} />
          <span className="text-sm text-muted-foreground">Token: {chunk.token_count}</span>
          {chunk.chunk_set_version != null && (
            <span className="text-sm text-muted-foreground">切分版本: v{chunk.chunk_set_version}</span>
          )}
        </div>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>分块内容</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="whitespace-pre-wrap text-sm font-mono bg-muted/50 rounded-lg p-4 max-h-96 overflow-auto">
            {chunk.content}
          </pre>
        </CardContent>
      </Card>

      {/* Generation panel */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>知识生成</CardTitle>
          <CardDescription>
            选择模板与模型并生成候选知识；缺一不可，均未选择时禁用生成。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3 mb-4">
            <select
              className="rounded border px-3 py-1.5 text-sm bg-transparent flex-1 max-w-xs"
              value={selectedTemplate}
              onChange={(e) => setSelectedTemplate(e.target.value)}
              disabled={showBusy ?? false}
              aria-label="模板"
            >
              {templates.length === 0 && <option value="">暂无模板</option>}
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.task_type})
                </option>
              ))}
            </select>
            <select
              className="rounded border px-3 py-1.5 text-sm bg-transparent flex-1 max-w-xs"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={showBusy ?? false}
              aria-label="模型"
            >
              {models.length === 0 && <option value="">暂无模型</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} ({m.model_name})
                </option>
              ))}
            </select>
            <Button
              onClick={handleGenerate}
              disabled={submitting || showBusy || !selectedTemplate || !selectedModel}
            >
              {submitting ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}
              生成
            </Button>
          </div>

          {(templates.length === 0 || models.length === 0) && (
            <p className="text-xs text-muted-foreground mb-2">
              缺少模板或模型配置，请先在设置中创建。
            </p>
          )}

          {track && (
            <div className="border rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">生成状态</span>
                <StatusBadge status={track.status} />
                {showBusy && <Loader2Icon className="size-3 animate-spin" />}
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
                <div className="mt-2 text-xs text-destructive">
                  {track.errorCode ? `${track.errorCode}: ` : ""}{track.errorMessage}
                </div>
              )}
              <div className="text-xs text-muted-foreground space-y-1">
                <div>
                  任务 ID: <span className="font-mono">{track.taskId}</span>
                </div>
                <div>
                  批次 ID: <span className="font-mono">{track.batchId}</span>
                </div>
              </div>

              {track.batch && (
                <div className="mt-3 text-xs border-t pt-2 space-y-1">
                  <div>
                    批次状态：<StatusBadge status={track.batch.status} />
                    {track.batch.completed_chunks}/{track.batch.total_chunks} chunks
                  </div>
                  {(() => {
                    const summary = asSummary(track.batch?.summary_json);
                    return summary ? (
                      <div className="text-muted-foreground">
                        成功 {summary.succeeded} / 失败 {summary.failed} / 取消 {summary.cancelled}
                      </div>
                    ) : null;
                  })()}
                  {track.batch.provenance_status !== "verified" && (
                    <div className="text-amber-600">
                      provenance: {track.batch.provenance_status}
                      {track.batch.provenance_error_code ? ` (${track.batch.provenance_error_code})` : ""}
                      ，不可作为可信导出来源。
                    </div>
                  )}
                  {track.batch.renderer_version && (
                    <div className="text-muted-foreground">
                      renderer: {track.batch.renderer_version}
                    </div>
                  )}
                </div>
              )}

              {/* 部分失败：展示成功/失败/取消计数，可定位失败列表。 */}
              {(() => {
                const summary = asSummary(track?.batch?.summary_json);
                return summary && (summary.failed > 0 || summary.cancelled > 0) ? (
                  <div className="mt-3 border-t pt-2">
                    <div className="text-sm font-medium mb-1">失败明细</div>
                    <ul className="text-xs space-y-1 text-destructive">
                      {summary.failures.map((f, i) => (
                        <li key={i}>
                          chunk: {String(f.chunk_id).slice(0, 8)} — {f.code}: {f.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null;
              })()}

              {/* retry 按钮：仅 verified 且 failed/cancelled 时可用。 */}
              {track.batch &&
                ["failed", "cancelled"].includes(track.batch.status) &&
                track.batch.provenance_status === "verified" && (
                  <Button
                    className="mt-3"
                    variant="outline"
                    size="sm"
                    onClick={handleRetry}
                    disabled={submitting}
                  >
                    <RotateCwIcon className="size-3" />
                    重试失败项
                  </Button>
                )}
            </div>
          )}

          {candidates.length > 0 && (
            <div className="mt-4 border-t pt-3">
              <div className="text-sm font-medium mb-2">候选结果</div>
              <ul className="space-y-2">
                {candidates.map((c) => (
                  <li key={c.id} className="text-xs border rounded p-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={c.status} />
                      <span className="font-mono">{c.id.slice(0, 8)}</span>
                    </div>
                    <pre className="mt-1 whitespace-pre-wrap bg-muted/50 rounded p-2 font-mono max-h-40 overflow-auto">
                      {JSON.stringify(c.content, null, 2)}
                    </pre>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
