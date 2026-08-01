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
import type { components } from "@/lib/api/generated";
import {
  ArrowLeftIcon,
  SaveIcon,
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

type TemplateDetail = components["schemas"]["PromptTemplateResponse"];

export default function TemplateEditorPage() {
  const params = useParams<{ id: string; tid: string }>();
  const projectId = params.id;
  const templateId = params.tid;

  const [template, setTemplate] = useState<TemplateDetail | null>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userPrompt, setUserPrompt] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .get("/projects/{pid}/prompt-templates/{tid}", {
        params: { pid: projectId, tid: templateId },
      })
      .then((data) => {
        setTemplate(data);
        setSystemPrompt(data.system_prompt || "");
        setUserPrompt(data.user_prompt_template || "");
      })
      .catch(() => toast.error("加载模板失败"))
      .finally(() => setLoading(false));
  }, [projectId, templateId]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await api.patch("/projects/{pid}/prompt-templates/{tid}", {
        system_prompt: systemPrompt,
        user_prompt_template: userPrompt,
      }, {
        params: { pid: projectId, tid: templateId },
      });
      toast.success("保存成功");
    } catch {
      toast.error("保存失败");
    } finally {
      setSaving(false);
    }
  }, [projectId, templateId, systemPrompt, userPrompt]);

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
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/templates`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回模板列表
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">{template.name}</h1>
            <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
              <span>{template.task_type}</span>
              <span>v{template.version}</span>
            </div>
          </div>
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

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>系统提示</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeMirrorEditor
              value={systemPrompt}
              onChange={setSystemPrompt}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>用户提示模板</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeMirrorEditor
              value={userPrompt}
              onChange={setUserPrompt}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
