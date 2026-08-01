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
import { EyeIcon, XIcon } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

type ExportRecord = components["schemas"]["ExportResponse"];
type SnapshotManifest = components["schemas"]["SnapshotManifestResponse"];

export default function ExportsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedManifest, setSelectedManifest] = useState<SnapshotManifest | null>(null);

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


    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState


    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。


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

  const columns: ColumnDef<ExportRecord>[] = [
    {
      key: "format",
      header: "格式",
      render: (row) => row.format,
    },
    {
      key: "dataset_id",
      header: "来源",
      render: (row) =>
        row.dataset_id ? "数据集" : row.benchmark_id ? "基准集" : "-",
    },
    {
      key: "item_count",
      header: "条目数",
      render: (row) => row.item_count,
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
        <div className="flex gap-1">
          <Button
            variant="ghost"
            size="xs"
            onClick={() => loadManifest(row)}
          >
            <EyeIcon className="size-3" />
            快照
          </Button>
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
                {formatJsonPreview(selectedManifest)}
              </pre>
            </ScrollArea>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
