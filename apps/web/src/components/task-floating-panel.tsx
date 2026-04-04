"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StatusBadge } from "@/components/status-badge";
import { Loader2Icon, ListTodoIcon } from "lucide-react";
import { api } from "@/lib/api";
import { useWs } from "@/hooks/use-ws";

interface TaskItem {
  id: string;
  task_type: string;
  status: string;
  progress?: number;
  created_at: string;
  error_message?: string;
}

interface TaskListResponse {
  items: TaskItem[];
  total: number;
}

const TASK_TYPE_LABELS: Record<string, string> = {
  parse: "文档解析",
  clean: "文档清洗",
  chunk: "文档分块",
  generate: "知识生成",
  export: "数据导出",
};

export function TaskFloatingPanel() {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const { subscribe } = useWs();

  const fetchTasks = useCallback(() => {
    api
      .get<TaskListResponse>("/tasks?page=1&page_size=20&status=running&status=pending")
      .then((data) => setTasks(data.items))
      .catch(() => {
        // ignore errors
      });
  }, []);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // Listen for task updates via WebSocket
  useEffect(() => {
    const unsub = subscribe("task_update", () => {
      fetchTasks();
    });
    return unsub;
  }, [subscribe, fetchTasks]);

  const runningCount = tasks.filter(
    (t) => t.status === "running" || t.status === "pending"
  ).length;

  return (
    <div className="fixed bottom-4 right-4 z-50">
      <Sheet>
        <SheetTrigger
          render={
            <Button
              variant="default"
              size="lg"
              className="rounded-full shadow-lg"
            />
          }
        >
          {runningCount > 0 ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <ListTodoIcon className="size-4" />
          )}
          <span className="ml-1">
            任务
          </span>
          {runningCount > 0 && (
            <Badge variant="secondary" className="ml-1">
              {runningCount}
            </Badge>
          )}
        </SheetTrigger>
        <SheetContent side="right">
          <SheetHeader>
            <SheetTitle>运行中的任务</SheetTitle>
          </SheetHeader>
          <ScrollArea className="flex-1 px-4">
            {tasks.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                暂无运行中的任务
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {tasks.map((task) => (
                  <div
                    key={task.id}
                    className="rounded-lg border p-3 text-sm"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-medium">
                        {TASK_TYPE_LABELS[task.task_type] || task.task_type}
                      </span>
                      <StatusBadge status={task.status} />
                    </div>
                    {task.progress != null && task.progress > 0 && (
                      <div className="mt-2">
                        <div className="flex justify-between text-xs text-muted-foreground mb-1">
                          <span>进度</span>
                          <span>{Math.round(task.progress * 100)}%</span>
                        </div>
                        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${task.progress * 100}%` }}
                          />
                        </div>
                      </div>
                    )}
                    {task.error_message && (
                      <div className="mt-1 text-xs text-destructive">
                        {task.error_message}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </div>
  );
}
