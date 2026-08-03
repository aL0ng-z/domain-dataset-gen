"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { usePagination } from "@/hooks/use-pagination";
import { api, formatJsonPreview } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import {
  DownloadIcon,
  EyeIcon,
  RotateCwIcon,
  ShieldCheckIcon,
  XIcon,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

type ExportRecord = components["schemas"]["ExportResponse"];
type SnapshotManifest = components["schemas"]["SnapshotManifestResponse"];

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  processing: "处理中",
  completed: "已完成",
  failed: "失败",
};

function StatusBadge({ status, integrity }: { status: string; integrity: string }) {
  const color =
    status === "completed"
      ? "bg-green-100 text-green-800"
      : status === "failed"
        ? "bg-red-100 text-red-800"
        : "bg-yellow-100 text-yellow-800";
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${color}`}>
      {STATUS_LABEL[status] ?? status}
      {integrity === "unverified_legacy" && (
        <span className="ml-1 rounded bg-orange-200 px-1 text-[10px] text-orange-900">历史不可验证</span>
      )}
    </span>
  );
}

export default function ExportsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedManifest, setSelectedManifest] = useState<SnapshotManifest | null>(null);
  const [verifying, setVerifying] = useState<Record<string, boolean>>({});

  const fetchExports = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/exports/", {
        params: { pid: projectId },
        query: { page, page_size: pageSize },
      })
      .then((data) => {
        setExports(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载导出记录失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize]);

  useEffect(() => {
    const timer = setTimeout(fetchExports, 0);
    return () => clearTimeout(timer);
  }, [fetchExports]);

  const loadManifest = useCallback(
    async (row: ExportRecord) => {
      try {
        const manifest = await api.get("/projects/{pid}/exports/{eid}/manifest", {
          params: { pid: projectId, eid: row.id },
        });
        setSelectedManifest(manifest);
      } catch {
        toast.error("加载快照清单失败");
      }
    },
    [projectId],
  );

  const handleDownload = useCallback(
    async (row: ExportRecord) => {
      // 链接始终指向后端 download endpoint（签发绑定 version 的 307），不缓存预签名 URL。
      window.open(`/api/projects/${projectId}/exports/${row.id}/download`, "_blank");
    },
    [projectId],
  );

  const handleVerify = useCallback(
    async (row: ExportRecord, deep: boolean) => {
      setVerifying((s) => ({ ...s, [row.id]: true }));
      try {
        const result = await api.get("/projects/{pid}/exports/{eid}/verify", {
          params: { pid: projectId, eid: row.id },
          query: deep ? { deep: "true" } : {},
        });
        if (result.status === "verified") {
          toast.success("完整性验证通过");
        } else {
          toast.error(`完整性验证失败：${result.status}`);
        }
      } catch {
        toast.error("验证失败");
      } finally {
        setVerifying((s) => ({ ...s, [row.id]: false }));
      }
    },
    [projectId],
  );

  const handleRetry = useCallback(async (row: ExportRecord) => {
    // T11：failed Export 通过 T07 retry 复用同一 export id（completed 返回 409 EXPORT_IMMUTABLE）。
    if (row.status === "completed") {
      toast.error("已完成的导出不可重跑");
      return;
    }
    if (!row.task_id) {
      toast.error("无关联任务，无法重试");
      return;
    }
    try {
      await api.post("/projects/{pid}/tasks/{tid}/retry", undefined, {
        params: { pid: projectId, tid: row.task_id },
        headers: { "Idempotency-Key": `retry-${row.task_id}-${Date.now()}` },
      });
      toast.success("已发起重试");
      setTimeout(fetchExports, 1000);
    } catch {
      toast.error("重试失败");
    }
  }, [projectId, fetchExports]);

  const columns: ColumnDef<ExportRecord>[] = [
    {
      key: "source_type",
      header: "来源",
      render: (row) => (row.source_type === "benchmark" ? "基准集" : "数据集"),
    },
    {
      key: "format",
      header: "格式",
      render: (row) => row.format,
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} integrity={row.integrity_status} />,
    },
    {
      key: "item_count",
      header: "条目数",
      render: (row) => row.item_count ?? "-",
    },
    {
      key: "output_sha256",
      header: "Hash",
      render: (row) => (row.output_sha256 ? row.output_sha256.slice(0, 10) : "-"),
    },
    {
      key: "file_size",
      header: "大小",
      render: (row) => (row.file_size != null ? `${row.file_size} B` : "-"),
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) => new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "completed_at",
      header: "完成时间",
      render: (row) => (row.completed_at ? new Date(row.completed_at).toLocaleString("zh-CN") : "-"),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <div className="flex gap-1">
          {row.status === "completed" && row.integrity_status !== "unverified_legacy" ? (
            <Button variant="ghost" size="xs" onClick={() => handleDownload(row)}>
              <DownloadIcon className="size-3" />
              下载
            </Button>
          ) : null}
          <Button variant="ghost" size="xs" onClick={() => loadManifest(row)}>
            <EyeIcon className="size-3" />
            快照
          </Button>
          {row.status === "completed" ? (
            <Button
              variant="ghost"
              size="xs"
              disabled={verifying[row.id]}
              onClick={() => handleVerify(row, false)}
            >
              <ShieldCheckIcon className="size-3" />
              {verifying[row.id] ? "验证中" : "验证"}
            </Button>
          ) : null}
          {row.status === "failed" ? (
            <Button variant="ghost" size="xs" onClick={() => handleRetry(row)}>
              <RotateCwIcon className="size-3" />
              重试
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">导出历史</h1>
          <p className="text-sm text-muted-foreground mt-1">
            查看导出记录、快照清单、验证与重试
          </p>
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={exports}
          total={total}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          rowKey={(row) => row.id}
        />
      )}

      {/* Snapshot manifest viewer */}
      {selectedManifest && (
        <Card className="mt-6">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>
                SnapshotManifest
                {selectedManifest.integrity_status === "unverified_legacy" && (
                  <span className="ml-2 rounded bg-orange-200 px-2 py-0.5 text-xs text-orange-900">
                    历史不可验证
                  </span>
                )}
              </CardTitle>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setSelectedManifest(null)}
              >
                <XIcon className="size-4" />
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <ScrollArea className="max-h-96">
              <pre className="text-xs font-mono whitespace-pre-wrap bg-muted/50 rounded-lg p-4">
                {formatJsonPreview(selectedManifest)}
              </pre>
            </ScrollArea>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
