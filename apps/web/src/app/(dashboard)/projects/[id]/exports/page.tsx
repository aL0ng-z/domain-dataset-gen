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
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, type PaginatedResponse } from "@/lib/api";
import { DownloadIcon, EyeIcon, XIcon } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

interface ExportRecord {
  id: string;
  export_type: string;
  format: string;
  status: string;
  file_size?: number;
  download_url?: string;
  snapshot_manifest?: Record<string, unknown>;
  created_at: string;
  finished_at?: string;
}

export default function ExportsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedManifest, setSelectedManifest] = useState<Record<
    string,
    unknown
  > | null>(null);

  const fetchExports = useCallback(() => {
    setLoading(true);
    api
      .get<PaginatedResponse<ExportRecord>>(
        `/projects/${projectId}/exports?page=${page}&page_size=${pageSize}`
      )
      .then((data) => {
        setExports(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载导出记录失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize]);

  useEffect(() => {
    fetchExports();
  }, [fetchExports]);

  const columns: ColumnDef<ExportRecord>[] = [
    {
      key: "export_type",
      header: "类型",
      render: (row) => row.export_type,
    },
    {
      key: "format",
      header: "格式",
      render: (row) => row.format,
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "file_size",
      header: "文件大小",
      render: (row) => {
        if (!row.file_size) return "-";
        if (row.file_size < 1024) return `${row.file_size} B`;
        if (row.file_size < 1024 * 1024)
          return `${(row.file_size / 1024).toFixed(1)} KB`;
        return `${(row.file_size / (1024 * 1024)).toFixed(1)} MB`;
      },
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) =>
        new Date(row.created_at).toLocaleString("zh-CN"),
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
      key: "actions",
      header: "操作",
      render: (row) => (
        <div className="flex gap-1">
          {row.download_url && (
            <a href={row.download_url} download>
              <Button variant="ghost" size="xs">
                <DownloadIcon className="size-3" />
                下载
              </Button>
            </a>
          )}
          {row.snapshot_manifest && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() =>
                setSelectedManifest(row.snapshot_manifest!)
              }
            >
              <EyeIcon className="size-3" />
              快照
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
          <h1 className="text-2xl font-semibold">导出历史</h1>
          <p className="text-sm text-muted-foreground mt-1">
            查看所有导出记录和快照清单
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
              <CardTitle>SnapshotManifest</CardTitle>
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
                {JSON.stringify(selectedManifest, null, 2)}
              </pre>
            </ScrollArea>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
