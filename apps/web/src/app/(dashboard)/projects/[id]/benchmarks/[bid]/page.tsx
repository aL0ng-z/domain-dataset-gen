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
import { EligibleItemPicker } from "@/components/eligible-item-picker";
import { useProjectAccess } from "@/hooks/use-project-access";
import { usePagination } from "@/hooks/use-pagination";
import { api, ApiErrorException, formatJsonPreview } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import {
  ArrowLeftIcon,
  TrashIcon,
  DownloadIcon,
  Loader2Icon,
  PlusIcon,
  LockIcon,
} from "lucide-react";

type BenchmarkDetail = components["schemas"]["BenchmarkDetailResponse"];
type BenchmarkCase = components["schemas"]["BenchmarkCaseDetailResponse"];
type ExportProfile = components["schemas"]["ExportProfileResponse"];
type ExportProfilesPage = components["schemas"]["PaginatedResponse_ExportProfileResponse_"];

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
  const [pickerOpen, setPickerOpen] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const { canEdit, canReview } = useProjectAccess(projectId);
  const [finalizeGate, setFinalizeGate] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get("/projects/{pid}/benchmarks/{bid}", {
        params: { pid: projectId, bid: benchmarkId },
      }),
      api.get("/projects/{pid}/benchmarks/{bid}/cases", {
        params: { pid: projectId, bid: benchmarkId },
        query: { page, page_size: pageSize },
      }),
      api
        .get("/projects/{pid}/export-profiles/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 50 },
        })
        .catch(() => ({ items: [] as ExportProfile[] } as ExportProfilesPage)),
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
      if (!window.confirm("确认移除该用例？移除后 composition revision/hash 将更新。")) {
        return;
      }
      setRemovingId(caseId);
      try {
        await api.delete("/projects/{pid}/benchmarks/{bid}/cases/{case_id}", {
          params: { pid: projectId, bid: benchmarkId, case_id: caseId },
        });
        toast.success("已移除");
        if (cases.length === 1 && page > 1) {
          setPage(page - 1);
        } else {
          fetchData();
        }
      } catch (err) {
        const e = err as ApiErrorException;
        if (e?.apiError?.code === "COMPOSITION_NOT_DRAFT") {
          toast.error("容器已 finalize，无法移除");
        } else {
          toast.error("移除失败");
        }
      } finally {
        setRemovingId(null);
      }
    },
    [projectId, benchmarkId, cases.length, page, setPage, fetchData],
  );

  const handleExport = useCallback(async () => {
    if (!selectedProfile) {
      toast.error("请选择导出配置");
      return;
    }
    if (!benchmark || benchmark.status !== "finalized") {
      toast.error("仅 finalize 的基准集可导出");
      return;
    }
    setExporting(true);
    try {
      // T11：请求携带 expected source revision/hash（一致性校验）。
      await api.post("/projects/{pid}/benchmarks/{bid}/export", {
        export_profile_id: selectedProfile,
        expected_source_revision: benchmark.composition_revision,
        expected_source_sha256: benchmark.composition_sha256,
      }, {
        params: { pid: projectId, bid: benchmarkId },
      });
      toast.success("导出任务已发起");
    } catch {
      toast.error("导出失败");
    } finally {
      setExporting(false);
    }
  }, [projectId, benchmarkId, selectedProfile, benchmark]);

  const handleFinalize = useCallback(async () => {
    if (!benchmark) return;
    const expected = `${benchmark.case_count} 用例 · revision ${benchmark.composition_revision} · ${benchmark.composition_sha256.slice(0, 12)}…`;
    if (!window.confirm(`确认冻结该基准集？\n${expected}\n冻结后不可再添加或移除用例。`)) {
      return;
    }
    setFinalizing(true);
    setFinalizeGate(null);
    try {
      const result = await api.post("/projects/{pid}/benchmarks/{bid}/finalize", {
        expected_revision: benchmark.composition_revision,
        expected_sha256: benchmark.composition_sha256,
      }, {
        params: { pid: projectId, bid: benchmarkId },
      });
      setBenchmark(result as BenchmarkDetail);
      toast.success("基准集已冻结");
      fetchData();
    } catch (err) {
      const e = err as ApiErrorException;
      if (e?.apiError?.code === "COMPOSITION_REVISION_CONFLICT") {
        setFinalizeGate("composition-conflict");
        toast.error("编组内容已变化，请刷新后重新确认");
        fetchData();
      } else if (e?.apiError?.code === "COMPOSITION_FINALIZE_GATE_FAILED") {
        setFinalizeGate("gate-failed");
        toast.error("冻结复核失败：条目资格已失效");
        fetchData();
      } else if (e?.apiError?.code === "COMPOSITION_HASH_INVALID") {
        setFinalizeGate("hash-invalid");
        toast.error("composition hash 校验异常，禁止继续冻结");
      } else {
        toast.error("冻结失败");
      }
    } finally {
      setFinalizing(false);
    }
  }, [projectId, benchmarkId, benchmark, fetchData]);

  const caseColumns: ColumnDef<BenchmarkCase>[] = [
    {
      key: "ordinal",
      header: "序号",
      render: (row) => <span>{row.ordinal}</span>,
    },
    {
      key: "preview",
      header: "内容预览",
      className: "max-w-md",
      render: (row) => {
        const pinned = row.curated_item.pinned_content as Record<string, unknown> | undefined;
        const question =
          typeof pinned?.question === "string" ? pinned.question : undefined;
        return (
          <div className="min-w-0">
            <div className="text-sm truncate">
              {question || formatJsonPreview(row.curated_item.pinned_content).slice(0, 60)}
            </div>
            <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
              <span>{row.curated_item.item_type}</span>
              {row.curated_item.current_revision !==
                row.curated_item.pinned_revision.version && (
                <span className="text-amber-600">
                  条目已修订到 v{row.curated_item.current_revision}（容器仍固定
                  v{row.curated_item.pinned_revision.version}）
                </span>
              )}
            </div>
          </div>
        );
      },
    },
    {
      key: "created_at",
      header: "添加时间",
      render: (row) => (
        <span className="text-xs text-muted-foreground">
          {new Date(row.created_at).toLocaleString()}
        </span>
      ),
    },
    ...(canEdit
      ? [
          {
            key: "actions",
            header: "操作",
            render: (row: BenchmarkCase) => (
              <Button
                variant="ghost"
                size="xs"
                disabled={removingId === row.id}
                onClick={() => handleRemoveCase(row.id)}
              >
                {removingId === row.id ? (
                  <Loader2Icon className="size-3 animate-spin" />
                ) : (
                  <TrashIcon className="size-3" />
                )}
                移除
              </Button>
            ),
          },
        ]
      : []),
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

  const finalized = benchmark?.status === "finalized";

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
              {benchmark && (
                <span className="text-xs text-muted-foreground font-mono">
                  rev {benchmark.composition_revision} ·{" "}
                  {benchmark.composition_sha256.slice(0, 12)}
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            {canEdit && !finalized && (
              <Button onClick={() => setPickerOpen(true)}>
                <PlusIcon className="size-4" />
                添加用例
              </Button>
            )}
            {canReview && !finalized && (
              <Button
                variant="outline"
                onClick={handleFinalize}
                disabled={finalizing}
              >
                {finalizing ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <LockIcon className="size-4" />
                )}
                冻结
              </Button>
            )}
          </div>
        </div>
        {finalized && (
          <div className="mt-2 text-xs text-muted-foreground flex items-center gap-3">
            <span>已冻结：rev {benchmark?.finalized_revision}</span>
            <span className="font-mono">{benchmark?.finalized_sha256}</span>
            {benchmark?.finalized_at && (
              <span>{new Date(benchmark.finalized_at).toLocaleString()}</span>
            )}
          </div>
        )}
        {finalizeGate === "composition-conflict" && (
          <div className="mt-2 text-sm text-amber-600">
            编组内容已变化：请确认最新 revision/hash 后重新冻结。
          </div>
        )}
        {finalizeGate === "gate-failed" && (
          <div className="mt-2 text-sm text-destructive">
            冻结复核失败：某条目已退审或不再满足资格，无法冻结。
          </div>
        )}
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
          {finalized && (
            <CardDescription>该基准集已冻结，仅可只读查看。</CardDescription>
          )}
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

      <EligibleItemPicker
        containerType="benchmark"
        projectId={projectId}
        containerId={benchmarkId}
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        onAdded={fetchData}
      />
    </div>
  );
}
