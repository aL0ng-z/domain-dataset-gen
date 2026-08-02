"use client";

import React, { useCallback, useEffect, useState } from "react";
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
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { ArrowLeftIcon, PlayIcon, Loader2Icon } from "lucide-react";

type ChunkDetail = components["schemas"]["ChunkResponse"];
type Template = components["schemas"]["PromptTemplateResponse"];
type GenerateTask = components["schemas"]["TaskResponse"];

export default function ChunkDetailPage() {
  const params = useParams<{ id: string; did: string; cid: string }>();
  const projectId = params.id;
  const docId = params.did;
  const chunkId = params.cid;

  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>("");
  const [generating, setGenerating] = useState(false);
  const [generateTask, setGenerateTask] = useState<GenerateTask | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get("/chunks/{cid}", { params: { cid: chunkId } }),
      api
        .get("/projects/{pid}/prompt-templates/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 100 },
        })
        .catch(() => ({ items: [] as Template[] })),
    ])
      .then(([chunkData, templateData]) => {
        setChunk(chunkData);
        setTemplates(templateData.items);
        if (templateData.items.length > 0) {
          setSelectedTemplate(templateData.items[0].id);
        }
      })
      .catch(() => toast.error("加载分块详情失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId, chunkId]);

  const handleGenerate = useCallback(async () => {
    if (!selectedTemplate) {
      toast.error("请选择模板");
      return;
    }
    setGenerating(true);
    setGenerateTask(null);
    try {
      const result = await api.post(
        "/chunks/{cid}/generate",
        {
          prompt_template_id: selectedTemplate,
          model_config_id: "",
        },
        { params: { cid: chunkId } },
      );
      setGenerateTask(result);
      toast.success("生成任务已创建");
    } catch {
      toast.error("生成失败");
    } finally {
      setGenerating(false);
    }
  }, [chunkId, selectedTemplate]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  if (!chunk) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          分块不存在
        </div>
      </div>
    );
  }

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
        <h1 className="text-2xl font-semibold">
          分块 #{chunk.ordinal + 1}
        </h1>
        <div className="flex items-center gap-2 mt-1">
          <StatusBadge status={chunk.status} />
          <span className="text-sm text-muted-foreground">
            Token: {chunk.token_count}
          </span>
          {chunk.chunk_set_version !== null && chunk.chunk_set_version !== undefined && (
            <span className="text-sm text-muted-foreground">
              切分版本: v{chunk.chunk_set_version}
            </span>
          )}
          {chunk.heading_path && (
            <span className="text-sm text-muted-foreground">
              路径: {chunk.heading_path}
            </span>
          )}
        </div>
        {chunk.chunk_set_version !== null && chunk.chunk_set_version !== undefined && (
          <p className="mt-1 text-xs text-muted-foreground">
            切分版本不可变，历史集合不可原地编辑。
          </p>
        )}
      </div>

      {/* Chunk content */}
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
      <Card>
        <CardHeader>
          <CardTitle>知识生成</CardTitle>
          <CardDescription>
            选择模板并生成候选知识
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3 mb-4">
            <select
              className="rounded border px-3 py-1.5 text-sm bg-transparent flex-1 max-w-xs"
              value={selectedTemplate}
              onChange={(e) => setSelectedTemplate(e.target.value)}
            >
              {templates.length === 0 && (
                <option value="">暂无模板</option>
              )}
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.task_type})
                </option>
              ))}
            </select>
            <Button
              onClick={handleGenerate}
              disabled={generating || !selectedTemplate}
            >
              {generating ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}
              生成
            </Button>
          </div>

          {generateTask && (
            <div className="border rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">生成任务</span>
                <StatusBadge status={generateTask.status} />
              </div>
              <div className="text-xs text-muted-foreground">
                任务 ID: <span className="font-mono">{generateTask.id}</span>
              </div>
              {generateTask.error_message && (
                <div className="mt-1 text-xs text-destructive">
                  {generateTask.error_message}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
