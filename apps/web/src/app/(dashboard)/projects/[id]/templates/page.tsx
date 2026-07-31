"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { api, type PaginatedResponse } from "@/lib/api";
import { usePagination } from "@/hooks/use-pagination";
import { PlusIcon, FileEditIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface Template {
  id: string;
  name: string;
  task_type: string;
  system_prompt?: string;
  user_prompt_template?: string;
  description?: string;
  created_at: string;
}

const TASK_TYPES = [
  { key: "all", label: "全部" },
  { key: "knowledge_extraction", label: "知识抽取" },
  { key: "qa_generation", label: "问答生成" },
  { key: "eval_case", label: "评测用例" },
];

export default function TemplatesPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    task_type: "knowledge_extraction",
    description: "",
    system_prompt: "",
    user_prompt_template: "",
  });

  const fetchTemplates = useCallback(() => {
    setLoading(true);
    const typeParam =
      activeTab !== "all" ? `&task_type=${activeTab}` : "";
    api
      .get<PaginatedResponse<Template>>(
        `/projects/${projectId}/prompt-templates?page=${page}&page_size=${pageSize}${typeParam}`
      )
      .then((data) => {
        setTemplates(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载模板列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize, activeTab]);

  useEffect(() => {


    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState


    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。


    const timer = setTimeout(fetchTemplates, 0);


    return () => clearTimeout(timer);


  }, [fetchTemplates]);

  const handleCreate = useCallback(async () => {
    if (!formData.name.trim()) {
      toast.error("请输入模板名称");
      return;
    }
    try {
      await api.post(`/projects/${projectId}/prompt-templates`, formData);
      toast.success("模板创建成功");
      setDialogOpen(false);
      setFormData({
        name: "",
        task_type: "knowledge_extraction",
        description: "",
        system_prompt: "",
        user_prompt_template: "",
      });
      fetchTemplates();
    } catch {
      toast.error("创建模板失败");
    }
  }, [projectId, formData, fetchTemplates]);

  const columns: ColumnDef<Template>[] = [
    {
      key: "name",
      header: "模板名称",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/templates/${row.id}`}
          className="font-medium text-primary hover:underline"
        >
          {row.name}
        </Link>
      ),
    },
    {
      key: "task_type",
      header: "任务类型",
      render: (row) => {
        const label = TASK_TYPES.find((t) => t.key === row.task_type)?.label;
        return label || row.task_type;
      },
    },
    {
      key: "description",
      header: "描述",
      render: (row) => row.description || "-",
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) =>
        new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Link href={`/projects/${projectId}/templates/${row.id}`}>
          <Button variant="ghost" size="xs">
            <FileEditIcon className="size-3" />
            编辑
          </Button>
        </Link>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">提示模板</h1>
          <p className="text-sm text-muted-foreground mt-1">
            管理LLM生成的提示模板
          </p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger render={<Button />}>
            <PlusIcon className="size-4" />
            新建模板
          </DialogTrigger>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>新建模板</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium">名称</label>
                <Input
                  value={formData.name}
                  onChange={(e) =>
                    setFormData({ ...formData, name: e.target.value })
                  }
                  placeholder="模板名称"
                />
              </div>
              <div>
                <label className="text-sm font-medium">任务类型</label>
                <select
                  className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                  value={formData.task_type}
                  onChange={(e) =>
                    setFormData({ ...formData, task_type: e.target.value })
                  }
                >
                  <option value="knowledge_extraction">知识抽取</option>
                  <option value="qa_generation">问答生成</option>
                  <option value="eval_case">评测用例</option>
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">描述</label>
                <Textarea
                  value={formData.description}
                  onChange={(e) =>
                    setFormData({ ...formData, description: e.target.value })
                  }
                  placeholder="模板描述"
                />
              </div>
              <div>
                <label className="text-sm font-medium">系统提示</label>
                <Textarea
                  value={formData.system_prompt}
                  onChange={(e) =>
                    setFormData({ ...formData, system_prompt: e.target.value })
                  }
                  placeholder="System prompt"
                  className="min-h-24"
                />
              </div>
              <div>
                <label className="text-sm font-medium">用户提示模板</label>
                <Textarea
                  value={formData.user_prompt_template}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      user_prompt_template: e.target.value,
                    })
                  }
                  placeholder="User prompt template（使用 {{chunk_content}} 等变量）"
                  className="min-h-24"
                />
              </div>
            </div>
            <DialogFooter>
              <Button onClick={handleCreate}>创建</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <Tabs
        defaultValue="all"
        value={activeTab}
        onValueChange={(v) => {
          setActiveTab(v);
          setPage(1);
        }}
      >
        <TabsList>
          {TASK_TYPES.map((t) => (
            <TabsTrigger key={t.key} value={t.key}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={activeTab}>
          {loading ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              加载中...
            </div>
          ) : (
            <DataTable
              columns={columns}
              data={templates}
              total={total}
              page={page}
              pageSize={pageSize}
              onPageChange={setPage}
              rowKey={(row) => row.id}
            />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
