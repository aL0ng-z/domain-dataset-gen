"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, type PaginatedResponse } from "@/lib/api";
import {
  ArrowLeftIcon,
  TrashIcon,
  DownloadIcon,
  Loader2Icon,
} from "lucide-react";

interface BenchmarkDetail {
  id: string;
  name: string;
  description?: string;
  case_count: number;
  status: string;
}

interface BenchmarkCase {
  id: string;
  curated_item_id: string;
  content_preview?: string;
  item_type?: string;
  created_at: string;
}

interface ExportProfile {
  id: string;
  name: string;
  format: string;
}

export default function BenchmarkDetailPage() {
  const params = useParams<{ id: string; bid: string }>();
  const projectId = params.id;
  const benchmarkId = params.bid;
  const { page, pageSize, setPage } = usePagination();

  const [benchmark, setBenchmark] = useState<BenchmarkDetail | null>(null);
  const [cases, setCases] = useState<BenchmarkCase[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [exportProfiles, setExportProfiles] = useState<ExportProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [exporting, setExporting] = useState(false);

  const fetchData = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get<BenchmarkDetail>(
        `/projects/${projectId}/benchmarks/${benchmarkId}`
      ),
      api.get<PaginatedResponse<BenchmarkCase>>(
        `/projects/${projectId}/benchmarks/${benchmarkId}/cases?page=${page}&page_size=${pageSize}`
      ),
      api
        .get<{ items: ExportProfile[] }>(
          `/projects/${projectId}/config/export-profiles?page=1&page_size=50`
        )
        .catch(() => ({ items: [] })),
    ])
      .then(([bm, casesData, profiles]) => {
        setBenchmark(bm);
        setCases(casesData.items);
        setTotal(casesData.total);
        setExportProfiles(profiles.items);
        if (profiles.items.length > 0 && !selectedProfile) {
          setSelectedProfile(profiles.items[0].id);
        }
      })
      .catch(() => toast.error("加载基准集失败"))
      .finally(() => setLoading(false));
  }, [projectId, benchmarkId, page, pageSize, selectedProfile]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleRemoveCase = useCallback(
    async (caseId: string) => {
      try {
        await api.delete(
          `/projects/${projectId}/benchmarks/${benchmarkId}/cases/${caseId}`
        );
        toast.success("已移除");
        fetchData();
      } catch {
        toast.error("移除失败");
      }
    },
    [projectId, benchmarkId, fetchData]
  );

  const handleExport = useCallback(async () => {
    if (!selectedProfile) {
      toast.error("请选择导出配置");
      return;
    }
    setExporting(true);
    try {
      await api.post(
        `/projects/${projectId}/benchmarks/${benchmarkId}/export`,
        { export_profile_id: selectedProfile }
      );
      toast.success("导出任务已发起");
    } catch {
      toast.error("导出失败");
    } finally {
      setExporting(false);
    }
  }, [projectId, benchmarkId, selectedProfile]);

  const caseColumns: ColumnDef<BenchmarkCase>[] = [
    {
      key: "content",
      header: "内容预览",
      className: "max-w-md",
      render: (row) => (
        <span className="truncate block max-w-md">
          {row.content_preview || "(空)"}
        </span>
      ),
    },
    {
      key: "item_type",
      header: "类型",
      render: (row) => row.item_type || "-",
    },
    {
      key: "created_at",
      header: "添加时间",
      render: (row) =>
        new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Button
          variant="ghost"
          size="xs"
          onClick={() => handleRemoveCase(row.id)}
        >
          <TrashIcon className="size-3" />
          移除
        </Button>
      ),
    },
  ];

  if (loading && !benchmark) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/benchmarks`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回基准集列表
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">
              {benchmark?.name || "基准集"}
            </h1>
            <div className="flex items-center gap-2 mt-1">
              {benchmark && <StatusBadge status={benchmark.status} />}
              <span className="text-sm text-muted-foreground">
                {total} 用例
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Export panel */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>导出</CardTitle>
          <CardDescription>选择导出配置并导出基准集</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <select
              className="rounded border px-3 py-1.5 text-sm bg-transparent flex-1 max-w-xs"
              value={selectedProfile}
              onChange={(e) => setSelectedProfile(e.target.value)}
            >
              {exportProfiles.length === 0 && (
                <option value="">暂无导出配置</option>
              )}
              {exportProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.format})
                </option>
              ))}
            </select>
            <div className="text-sm text-muted-foreground">
              共 {total} 用例
            </div>
            <Button
              onClick={handleExport}
              disabled={exporting || !selectedProfile}
            >
              {exporting ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <DownloadIcon className="size-4" />
              )}
              导出
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Cases table */}
      <Card>
        <CardHeader>
          <CardTitle>评测用例</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={caseColumns}
            data={cases}
            total={total}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            rowKey={(row) => row.id}
          />
        </CardContent>
      </Card>
    </div>
  );
}
