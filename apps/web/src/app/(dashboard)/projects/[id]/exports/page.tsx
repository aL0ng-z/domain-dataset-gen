"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
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
type VerifyResult = components["schemas"]["ExportVerifyResponse"];

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

function VerificationSummary({ result }: { result: VerifyResult }) {
  if (result.status !== "verified") {
    return <span className="text-xs text-red-700">验证失败</span>;
  }
  if (result.deep) {
    const passed = result.deep.filter((item) => item.ok).length;
    return (
      <span className="text-xs text-green-700">
        深验 {passed}/{result.deep.length}
      </span>
    );
  }
  return (
    <span className="text-xs text-green-700">
      浅验{result.shallow.metadata_ok ? "通过" : "待复核"}
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
  const [downloading, setDownloading] = useState<Record<string, boolean>>({});
  const [retrying, setRetrying] = useState<Record<string, boolean>>({});
  const [verification, setVerification] = useState<Record<string, VerifyResult>>({});
  const retryKeys = useRef<Record<string, string>>({});

  const fetchExports = useCallback((silent = false) => {
    if (!silent) setLoading(true);
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
      .finally(() => {
        if (!silent) setLoading(false);
      });
  }, [projectId, page, pageSize]);

  useEffect(() => {
    const timer = setTimeout(fetchExports, 0);
    return () => clearTimeout(timer);
  }, [fetchExports]);

  useEffect(() => {
    if (!exports.some((row) => row.status === "queued" || row.status === "processing")) return;
    const timer = window.setInterval(() => fetchExports(true), 3000);
    return () => window.clearInterval(timer);
  }, [exports, fetchExports]);

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
      if (verification[row.id]?.status === "failed" || downloading[row.id]) return;
      setDownloading((state) => ({ ...state, [row.id]: true }));
      try {
        const link = await api.post(
          "/projects/{pid}/exports/{eid}/download-link",
          undefined,
          { params: { pid: projectId, eid: row.id } },
        );
        const anchor = document.createElement("a");
        anchor.href = link.url;
        anchor.download = link.filename;
        anchor.rel = "noopener noreferrer";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } catch {
        toast.error("下载链接签发失败");
      } finally {
        setDownloading((state) => ({ ...state, [row.id]: false }));
      }
    },
    [projectId, downloading, verification],
  );

  const handleVerify = useCallback(
    async (row: ExportRecord, deep: boolean) => {
      setVerifying((s) => ({ ...s, [row.id]: true }));
      try {
        const result = await api.post("/projects/{pid}/exports/{eid}/verify", undefined, {
          params: { pid: projectId, eid: row.id },
          query: { deep },
        });
        setVerification((state) => ({ ...state, [row.id]: result }));
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
    if (retrying[row.id]) return;
    setRetrying((state) => ({ ...state, [row.id]: true }));
    const key = retryKeys.current[row.task_id] ?? `export-retry-${crypto.randomUUID()}`;
    retryKeys.current[row.task_id] = key;
    try {
      const task = await api.post("/projects/{pid}/tasks/{tid}/retry", undefined, {
        params: { pid: projectId, tid: row.task_id },
        headers: { "Idempotency-Key": key },
      });
      delete retryKeys.current[row.task_id];
      setExports((items) => items.map((item) => (
        item.id === row.id ? { ...item, status: "queued", task_id: task.id } : item
      )));
      toast.success("已发起重试");
      window.setTimeout(() => fetchExports(true), 1000);
    } catch {
      toast.error("重试失败");
    } finally {
      setRetrying((state) => ({ ...state, [row.id]: false }));
    }
  }, [projectId, fetchExports, retrying]);

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
            <Button
              variant="ghost"
              size="xs"
              disabled={downloading[row.id] || verification[row.id]?.status === "failed"}
              onClick={() => handleDownload(row)}
            >
              <DownloadIcon className="size-3" />
              {downloading[row.id] ? "签发中" : "下载"}
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
              {verifying[row.id] ? "验证中" : "浅验"}
            </Button>
          ) : null}
          {row.status === "completed" ? (
            <Button
              variant="ghost"
              size="xs"
              disabled={verifying[row.id]}
              onClick={() => handleVerify(row, true)}
            >
              <ShieldCheckIcon className="size-3" />
              深验
            </Button>
          ) : null}
          {row.status === "failed" ? (
            <Button
              variant="ghost"
              size="xs"
              disabled={retrying[row.id]}
              onClick={() => handleRetry(row)}
            >
              <RotateCwIcon className="size-3" />
              {retrying[row.id] ? "重试中" : "重试"}
            </Button>
          ) : null}
          {verification[row.id] ? (
            <VerificationSummary result={verification[row.id]} />
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
