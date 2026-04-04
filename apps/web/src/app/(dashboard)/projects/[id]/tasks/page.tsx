"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, type PaginatedResponse } from "@/lib/api";
import { useWs } from "@/hooks/use-ws";
import { RefreshCwIcon, XCircleIcon, RotateCwIcon } from "lucide-react";

interface TaskItem {
  id: string;
  task_type: string;
  status: string;
  progress?: number;
  error_message?: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
}

const TASK_TYPE_LABELS: Record<string, string> = {
  parse: "文档解析",
  clean: "文档清洗",
  chunk: "文档分块",
  generate: "知识生成",
  export: "数据导出",
};

const TYPE_OPTIONS = [
  { value: "all", label: "全部类型" },
  { value: "parse", label: "文档解析" },
  { value: "clean", label: "文档清洗" },
  { value: "chunk", label: "文档分块" },
  { value: "generate", label: "知识生成" },
  { value: "export", label: "数据导出" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "pending", label: "待处理" },
  { value: "running", label: "运行中" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

export default function TasksPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const { subscribe } = useWs();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const fetchTasks = useCallback(() => {
    setLoading(true);
    const typeParam =
      typeFilter !== "all" ? `&task_type=${typeFilter}` : "";
    const statusParam =
      statusFilter !== "all" ? `&status=${statusFilter}` : "";
    api
      .get<PaginatedResponse<TaskItem>>(
        `/projects/${projectId}/tasks?page=${page}&page_size=${pageSize}${typeParam}${statusParam}`
      )
      .then((data) => {
        setTasks(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载任务列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize, typeFilter, statusFilter]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // WebSocket live updates
  useEffect(() => {
    const unsub = subscribe("task_update", () => {
      fetchTasks();
    });
    return unsub;
  }, [subscribe, fetchTasks]);

  const handleCancel = useCallback(
    async (taskId: string) => {
      try {
        await api.post(
          `/projects/${projectId}/tasks/${taskId}/cancel`
        );
        toast.success("任务已取消");
        fetchTasks();
      } catch {
        toast.error("取消失败");
      }
    },
    [projectId, fetchTasks]
  );

  const handleRetry = useCallback(
    async (taskId: string) => {
      try {
        await api.post(
          `/projects/${projectId}/tasks/${taskId}/retry`
        );
        toast.success("已重试任务");
        fetchTasks();
      } catch {
        toast.error("重试失败");
      }
    },
    [projectId, fetchTasks]
  );

  const columns: ColumnDef<TaskItem>[] = [
    {
      key: "task_type",
      header: "类型",
      render: (row) =>
        TASK_TYPE_LABELS[row.task_type] || row.task_type,
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "progress",
      header: "进度",
      render: (row) => {
        if (row.progress == null) return "-";
        const pct = Math.round(row.progress * 100);
        return (
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-20 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-xs tabular-nums">{pct}%</span>
          </div>
        );
      },
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) =>
        new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "started_at",
      header: "开始时间",
      render: (row) =>
        row.started_at
          ? new Date(row.started_at).toLocaleString("zh-CN")
          : "-",
    },
    {
      key: "finished_at",
      header: "完成时间",
      render: (row) =>
        row.finished_at
          ? new Date(row.finished_at).toLocaleString("zh-CN")
          : "-",
    },
    {
      key: "error",
      header: "错误",
      render: (row) =>
        row.error_message ? (
          <span
            className="text-xs text-destructive truncate block max-w-xs"
            title={row.error_message}
          >
            {row.error_message}
          </span>
        ) : (
          "-"
        ),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <div className="flex gap-1">
          {(row.status === "pending" || row.status === "running") && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => handleCancel(row.id)}
            >
              <XCircleIcon className="size-3" />
              取消
            </Button>
          )}
          {row.status === "failed" && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => handleRetry(row.id)}
            >
              <RotateCwIcon className="size-3" />
              重试
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">任务中心</h1>
          <p className="text-sm text-muted-foreground mt-1">
            查看和管理所有后台任务
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="rounded border px-3 py-1.5 text-sm bg-transparent"
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value);
              setPage(1);
            }}
          >
            {TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            className="rounded border px-3 py-1.5 text-sm bg-transparent"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(1);
            }}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" onClick={fetchTasks}>
            <RefreshCwIcon className="size-3" />
            刷新
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={tasks}
          total={total}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          rowKey={(row) => row.id}
        />
      )}
    </div>
  );
}
