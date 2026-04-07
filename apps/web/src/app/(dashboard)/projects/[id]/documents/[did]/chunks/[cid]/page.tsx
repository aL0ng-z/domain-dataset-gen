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
import { ArrowLeftIcon, PlayIcon, Loader2Icon } from "lucide-react";

interface ChunkDetail {
  id: string;
  chunk_index: number;
  section_id?: string;
  section_title?: string;
  heading_path?: string;
  token_count: number;
  status: string;
  content: string;
  created_at: string;
}

interface Template {
  id: string;
  name: string;
  task_type: string;
}

interface Candidate {
  id: string;
  status: string;
  content?: string;
  template_name?: string;
  created_at: string;
}

export default function ChunkDetailPage() {
  const params = useParams<{ id: string; did: string; cid: string }>();
  const projectId = params.id;
  const docId = params.did;
  const chunkId = params.cid;

  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>("");
  const [generating, setGenerating] = useState(false);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get<ChunkDetail>(
        `/chunks/${chunkId}`
      ),
      api
        .get<{ items: Template[] }>(
          `/projects/${projectId}/prompt-templates?page=1&page_size=100`
        )
        .catch(() => ({ items: [] })),
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
    setCandidate(null);
    try {
      const result = await api.post<Candidate>(
        `/chunks/${chunkId}/generate`,
        { template_id: selectedTemplate }
      );
      setCandidate(result);
      toast.success("生成完成");
    } catch {
      toast.error("生成失败");
    } finally {
      setGenerating(false);
    }
  }, [projectId, docId, chunkId, selectedTemplate]);

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
          分块 #{chunk.chunk_index + 1}
        </h1>
        <div className="flex items-center gap-2 mt-1">
          <StatusBadge status={chunk.status} />
          <span className="text-sm text-muted-foreground">
            Token: {chunk.token_count}
          </span>
          {chunk.heading_path && (
            <span className="text-sm text-muted-foreground">
              路径: {chunk.heading_path}
            </span>
          )}
        </div>
      </div>

      {/* Chunk content */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>分块内容</CardTitle>
          {chunk.section_title && (
            <CardDescription>
              所属章节: {chunk.section_title}
            </CardDescription>
          )}
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

          {candidate && (
            <div className="border rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">生成结果</span>
                <StatusBadge status={candidate.status} />
              </div>
              <pre className="whitespace-pre-wrap text-sm font-mono bg-muted/50 rounded p-3 max-h-96 overflow-auto">
                {candidate.content || "无内容"}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
