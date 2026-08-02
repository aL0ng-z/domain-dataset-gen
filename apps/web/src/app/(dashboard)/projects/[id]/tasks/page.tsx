"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { useWs } from "@/hooks/use-ws";
import { RefreshCwIcon, XCircleIcon, RotateCwIcon } from "lucide-react";

type TaskItem = components["schemas"]["TaskResponse"];

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
  { value: "queued", label: "排队中" },
  { value: "processing", label: "处理中" },
  { value: "cancelling", label: "取消中" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

export default function TasksPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const { lastMessage } = useWs();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  // 乱序丢弃：记录每个 task 已见的最高 state_version。
  const seenStateVersion = useRef<Record<string, number>>({});

  const fetchTasks = useCallback(
    (silent = false) => {
      if (!silent) setLoading(true);
      api
        .get("/projects/{pid}/tasks/", {
          params: { pid: projectId },
          query: {
            page,
            page_size: pageSize,
            task_type: typeFilter !== "all" ? typeFilter : undefined,
            status: statusFilter !== "all" ? statusFilter : undefined,
          },
        })
        .then((data) => {
          setTasks(data.items);
          setTotal(data.total);
          // 重置乱序基线：以 REST 快照为准。
          const baseline: Record<string, number> = {};
          data.items.forEach((t) => {
            baseline[t.id] = t.state_version ?? 0;
          });
          seenStateVersion.current = baseline;
        })
        .catch(() => toast.error("加载任务列表失败"))
        .finally(() => {
          if (!silent) setLoading(false);
        });
    },
    [projectId, page, pageSize, typeFilter, statusFilter]
  );

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => fetchTasks(), 0);
    return () => window.clearTimeout(refreshTimer);
  }, [fetchTasks]);

  // WebSocket 事件：按 state_version 丢弃乱序消息；收到事件后重新 GET。
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as {
      event?: string;
      task_id?: string;
      state_version?: number;
    };
    if (!msg.task_id) return;
    const version = msg.state_version ?? 0;
    const seen = seenStateVersion.current[msg.task_id] ?? 0;
    if (version < seen) return; // 乱序消息丢弃
    const refreshTimer = window.setTimeout(() => fetchTasks(true), 0);
    return () => window.clearTimeout(refreshTimer);
  }, [lastMessage, fetchTasks]);

  // 轮询兜底：存在 processing/cancelling 任务时，即使无 WS 事件也定期刷新。
  useEffect(() => {
    const hasActive = tasks.some(
      (t) => t.status === "processing" || t.status === "cancelling"
    );
    if (!hasActive) return;
    const timer = window.setInterval(() => {
      fetchTasks(true);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [tasks, fetchTasks]);

  const handleCancel = useCallback(
    async (taskId: string) => {
      try {
        await api.post("/projects/{pid}/tasks/{tid}/cancel", undefined, {
          params: { pid: projectId, tid: taskId },
        });
        // 点击后立即刷新显示"取消中"，不谎报"已取消"。
        toast.success("已请求取消");
        fetchTasks(true);
      } catch {
        toast.error("取消失败");
      }
    },
    [projectId, fetchTasks]
  );

  const handleRetry = useCallback(
    async (taskId: string) => {
      // 一次点击一个 idempotency key；成功后定位到新 Task。
      const key = `retry-${taskId}-${Date.now()}`;
      try {
        const data = await api.post("/projects/{pid}/tasks/{tid}/retry", undefined, {
          params: { pid: projectId, tid: taskId },
          headers: { "Idempotency-Key": key },
        });
        toast.success("已创建重试任务");
        // 定位到返回的新 Task，而不是等待旧 Task 变 queued。
        fetchTasks(true);
        const newTask = data as TaskItem;
        if (newTask && newTask.id) {
          toast.success(`重试任务已创建：${newTask.id.slice(0, 8)}`);
        }
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
      render: (row) => TASK_TYPE_LABELS[row.task_type] || row.task_type,
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "progress",
      header: "执行情况",
      render: (row) => {
        if (row.task_type === "parse") {
          const text = {
            queued: "等待解析",
            processing: "解析处理中",
            cancelling: "取消中",
            completed: "解析结果已生成",
            failed: "解析失败",
            cancelled: "已取消",
          }[row.status] ?? row.status;
          return <span className="text-xs text-muted-foreground">{text}</span>;
        }
        const pct = Math.min(row.progress ?? 0, 100);
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
      key: "attempt",
      header: "尝试次数",
      render: (row) => (
        <span className="text-xs tabular-nums">
          {row.attempt_count}/{row.max_attempts}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) => new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "next_run_at",
      header: "下次执行",
      render: (row) =>
        row.status === "queued" && row.next_run_at
          ? new Date(row.next_run_at).toLocaleString("zh-CN")
          : "-",
    },
    {
      key: "completed_at",
      header: "完成时间",
      render: (row) =>
        row.completed_at
          ? new Date(row.completed_at).toLocaleString("zh-CN")
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
            {row.error_code ? `[${row.error_code}] ` : ""}
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
          {row.can_cancel && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => handleCancel(row.id)}
            >
              <XCircleIcon className="size-3" />
              {row.status === "cancelling" ? "取消中" : "取消"}
            </Button>
          )}
          {row.can_retry && (
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
          <Button variant="outline" size="sm" onClick={() => fetchTasks()}>
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
