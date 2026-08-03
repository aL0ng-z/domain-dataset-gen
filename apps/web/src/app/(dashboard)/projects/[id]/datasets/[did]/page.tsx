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

type DatasetDetail = components["schemas"]["DatasetDetailResponse"];
type DatasetItem = components["schemas"]["DatasetItemDetailResponse"];
type ExportProfile = components["schemas"]["ExportProfileResponse"];
type ExportProfilesPage = components["schemas"]["PaginatedResponse_ExportProfileResponse_"];

const ROLE_LEVEL: Record<string, number> = {
  admin: 4,
  reviewer: 3,
  editor: 2,
  viewer: 1,
};

export default function DatasetDetailPage() {
  const params = useParams<{ id: string; did: string }>();
  const projectId = params.id;
  const datasetId = params.did;
  const { page, pageSize, setPage } = usePagination();

  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [exportProfiles, setExportProfiles] = useState<ExportProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [exporting, setExporting] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const [canEdit, setCanEdit] = useState(false); // editor 以上
  const [canReview, setCanReview] = useState(false); // reviewer 以上
  const [finalizeGate, setFinalizeGate] = useState<string | null>(null); // 服务端 409 门禁提示

  const fetchData = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get("/projects/{pid}/datasets/{did}", {
        params: { pid: projectId, did: datasetId },
      }),
      api.get("/projects/{pid}/datasets/{did}/items", {
        params: { pid: projectId, did: datasetId },
        query: { page, page_size: pageSize },
      }),
      api
        .get("/projects/{pid}/export-profiles/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 50 },
        })
        .catch(() => ({ items: [] as ExportProfile[] } as ExportProfilesPage)),
      api
        .get("/auth/me")
        .then((u) => (u as { role?: string }).role)
        .catch(() => undefined),
    ])
      .then(([ds, itemsData, profiles, role]) => {
        setDataset(ds);
        setItems(itemsData.items);
        setTotal(itemsData.total);
        setExportProfiles(profiles.items);
        const level = role ? ROLE_LEVEL[role] ?? 0 : 0;
        setCanEdit(level >= (ROLE_LEVEL.editor ?? 0));
        setCanReview(level >= (ROLE_LEVEL.reviewer ?? 0));
        if (profiles.items.length > 0 && !selectedProfile) {
          setSelectedProfile(profiles.items[0].id);
        }
      })
      .catch(() => toast.error("加载数据集失败"))
      .finally(() => setLoading(false));
  }, [projectId, datasetId, page, pageSize, selectedProfile]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleRemoveItem = useCallback(
    async (itemId: string) => {
      if (!window.confirm("确认移除该条目？移除后 composition revision/hash 将更新。")) {
        return;
      }
      setRemovingId(itemId);
      try {
        await api.delete("/projects/{pid}/datasets/{did}/items/{item_id}", {
          params: { pid: projectId, did: datasetId, item_id: itemId },
        });
        toast.success("已移除");
        // 当前页被删空且 page > 1 时回退一页。
        if (items.length === 1 && page > 1) {
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
    [projectId, datasetId, items.length, page, setPage, fetchData],
  );

  const handleExport = useCallback(async () => {
    if (!selectedProfile) {
      toast.error("请选择导出配置");
      return;
    }
    if (!dataset || dataset.status !== "finalized") {
      toast.error("仅 finalize 的数据集可导出");
      return;
    }
    setExporting(true);
    try {
      // T11：请求携带 expected source revision/hash（一致性校验）。
      await api.post("/projects/{pid}/datasets/{did}/export", {
        export_profile_id: selectedProfile,
        expected_source_revision: dataset.composition_revision,
        expected_source_sha256: dataset.composition_sha256,
      }, {
        params: { pid: projectId, did: datasetId },
      });
      toast.success("导出任务已发起");
    } catch {
      toast.error("导出失败");
    } finally {
      setExporting(false);
    }
  }, [projectId, datasetId, selectedProfile, dataset]);

  const handleFinalize = useCallback(async () => {
    if (!dataset) return;
    const expected = `${dataset.item_count} 条目 · revision ${dataset.composition_revision} · ${dataset.composition_sha256.slice(0, 12)}…`;
    if (!window.confirm(`确认冻结该数据集？\n${expected}\n冻结后不可再添加或移除条目。`)) {
      return;
    }
    setFinalizing(true);
    setFinalizeGate(null);
    try {
      const result = await api.post("/projects/{pid}/datasets/{did}/finalize", {
        expected_revision: dataset.composition_revision,
        expected_sha256: dataset.composition_sha256,
      }, {
        params: { pid: projectId, did: datasetId },
      });
      setDataset(result as DatasetDetail);
      toast.success("数据集已冻结");
      fetchData();
    } catch (err) {
      const e = err as ApiErrorException;
      if (e?.apiError?.code === "COMPOSITION_REVISION_CONFLICT") {
        // 期间发生 add/remove：刷新并要求重新确认（不能忽略后继续）。
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
  }, [projectId, datasetId, dataset, fetchData]);

  const itemColumns: ColumnDef<DatasetItem>[] = [
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
            render: (row: DatasetItem) => (
              <Button
                variant="ghost"
                size="xs"
                disabled={removingId === row.id}
                onClick={() => handleRemoveItem(row.id)}
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

  if (loading && !dataset) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  const finalized = dataset?.status === "finalized";

  return (
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/datasets`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回数据集列表
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">
              {dataset?.name || "数据集"}
            </h1>
            <div className="flex items-center gap-2 mt-1">
              {dataset && <StatusBadge status={dataset.status} />}
              <span className="text-sm text-muted-foreground">
                {total} 条目
              </span>
              {dataset && (
                <span className="text-xs text-muted-foreground font-mono">
                  rev {dataset.composition_revision} ·{" "}
                  {dataset.composition_sha256.slice(0, 12)}
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            {canEdit && !finalized && (
              <Button onClick={() => setPickerOpen(true)}>
                <PlusIcon className="size-4" />
                添加条目
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
            <span>已冻结：rev {dataset?.finalized_revision}</span>
            <span className="font-mono">{dataset?.finalized_sha256}</span>
            {dataset?.finalized_at && (
              <span>{new Date(dataset.finalized_at).toLocaleString()}</span>
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
          <CardDescription>选择导出配置并导出数据集</CardDescription>
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
              共 {total} 条
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

      {/* Items table */}
      <Card>
        <CardHeader>
          <CardTitle>数据条目</CardTitle>
          {finalized && (
            <CardDescription>该数据集已冻结，仅可只读查看。</CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <DataTable
            columns={itemColumns}
            data={items}
            total={total}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            rowKey={(row) => row.id}
          />
        </CardContent>
      </Card>

      <EligibleItemPicker
        containerType="dataset"
        projectId={projectId}
        containerId={datasetId}
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        onAdded={fetchData}
      />
    </div>
  );
}
