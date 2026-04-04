"use client";

import React, { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import {
  ArrowLeftIcon,
  SaveIcon,
  PlayIcon,
  Loader2Icon,
} from "lucide-react";

const CodeMirrorEditor = dynamic(
  () =>
    import("../../documents/[did]/clean/codemirror-editor").then((m) => ({
      default: m.CodeMirrorEditor,
    })),
  {
    ssr: false,
    loading: () => (
      <div className="p-4 text-sm text-muted-foreground">
        编辑器加载中...
      </div>
    ),
  }
);

interface TemplateDetail {
  id: string;
  name: string;
  task_type: string;
  system_prompt: string;
  user_prompt_template: string;
  description?: string;
}

interface ChunkItem {
  id: string;
  chunk_index: number;
  content_preview?: string;
  section_title?: string;
}

export default function TemplateEditorPage() {
  const params = useParams<{ id: string; tid: string }>();
  const projectId = params.id;
  const templateId = params.tid;

  const [template, setTemplate] = useState<TemplateDetail | null>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userPrompt, setUserPrompt] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Test run state
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [selectedChunk, setSelectedChunk] = useState<string>("");
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState<string>("");

  useEffect(() => {
    api
      .get<TemplateDetail>(
        `/projects/${projectId}/templates/${templateId}`
      )
      .then((data) => {
        setTemplate(data);
        setSystemPrompt(data.system_prompt || "");
        setUserPrompt(data.user_prompt_template || "");
      })
      .catch(() => toast.error("加载模板失败"))
      .finally(() => setLoading(false));

    // Fetch some chunks for test run
    api
      .get<{ items: ChunkItem[] }>(
        `/projects/${projectId}/chunks?page=1&page_size=50`
      )
      .then((data) => {
        setChunks(data.items);
        if (data.items.length > 0) {
          setSelectedChunk(data.items[0].id);
        }
      })
      .catch(() => {});
  }, [projectId, templateId]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await api.put(`/projects/${projectId}/templates/${templateId}`, {
        system_prompt: systemPrompt,
        user_prompt_template: userPrompt,
      });
      toast.success("保存成功");
    } catch {
      toast.error("保存失败");
    } finally {
      setSaving(false);
    }
  }, [projectId, templateId, systemPrompt, userPrompt]);

  const handleTestRun = useCallback(async () => {
    if (!selectedChunk) {
      toast.error("请选择一个分块");
      return;
    }
    setTestRunning(true);
    setTestResult("");
    try {
      const result = await api.post<{ content: string }>(
        `/projects/${projectId}/templates/${templateId}/test-run`,
        { chunk_id: selectedChunk }
      );
      setTestResult(result.content || "(空结果)");
    } catch {
      toast.error("试跑失败");
    } finally {
      setTestRunning(false);
    }
  }, [projectId, templateId, selectedChunk]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  if (!template) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          模板不存在
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 h-full flex flex-col">
      <div className="mb-4">
        <Link
          href={`/projects/${projectId}/templates`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回模板列表
        </Link>
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">{template.name}</h1>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SaveIcon className="size-4" />
            )}
            保存
          </Button>
        </div>
      </div>

      {/* Split pane: 50/50 */}
      <div className="flex-1 grid grid-cols-2 gap-4 min-h-0">
        {/* Left: editors */}
        <div className="flex flex-col gap-4 min-h-0">
          <Card className="flex-1 flex flex-col min-h-0">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">系统提示 (System Prompt)</CardTitle>
            </CardHeader>
            <CardContent className="flex-1 min-h-0">
              <div className="h-full border rounded overflow-auto">
                <CodeMirrorEditor
                  value={systemPrompt}
                  onChange={setSystemPrompt}
                />
              </div>
            </CardContent>
          </Card>
          <Card className="flex-1 flex flex-col min-h-0">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                用户提示模板 (User Prompt Template)
              </CardTitle>
            </CardHeader>
            <CardContent className="flex-1 min-h-0">
              <div className="h-full border rounded overflow-auto">
                <CodeMirrorEditor
                  value={userPrompt}
                  onChange={setUserPrompt}
                />
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right: test run */}
        <Card className="flex flex-col min-h-0">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">试跑测试</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col min-h-0 gap-3">
            <div className="flex items-center gap-2">
              <select
                className="flex-1 rounded border px-3 py-1.5 text-sm bg-transparent"
                value={selectedChunk}
                onChange={(e) => setSelectedChunk(e.target.value)}
              >
                {chunks.length === 0 && (
                  <option value="">暂无可用分块</option>
                )}
                {chunks.map((c) => (
                  <option key={c.id} value={c.id}>
                    #{c.chunk_index + 1}{" "}
                    {c.section_title ? `(${c.section_title})` : ""}{" "}
                    {c.content_preview?.slice(0, 40)}
                  </option>
                ))}
              </select>
              <Button
                onClick={handleTestRun}
                disabled={testRunning || !selectedChunk}
              >
                {testRunning ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <PlayIcon className="size-4" />
                )}
                试跑
              </Button>
            </div>
            <div className="flex-1 border rounded p-3 overflow-auto bg-muted/30">
              {testResult ? (
                <pre className="whitespace-pre-wrap text-sm font-mono">
                  {testResult}
                </pre>
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  {testRunning ? "生成中..." : "点击「试跑」查看结果"}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
