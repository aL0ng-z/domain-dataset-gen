"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, formatJsonPreview } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { Button } from "@/components/ui/button";

type CuratedItem = components["schemas"]["CuratedItemResponse"];

const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "draft", label: "草稿" },
  { value: "approved", label: "已批准" },
  { value: "exported", label: "已导出" },
  { value: "deprecated", label: "已废弃" },
];

const TYPE_OPTIONS = [
  { value: "all", label: "全部类型" },
  { value: "knowledge", label: "知识" },
  { value: "qa", label: "问答" },
  { value: "eval_case", label: "评测用例" },
];

export default function CuratedPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [items, setItems] = useState<CuratedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");

  const fetchItems = useCallback(() => {
    setLoading(true);
    const statusParam =
      statusFilter !== "all" ? statusFilter : undefined;
    const typeParam =
      typeFilter !== "all" ? typeFilter : undefined;
    api
      .get("/projects/{pid}/curated-items/", {
        params: { pid: projectId },
        query: {
          page,
          page_size: pageSize,
          status: statusParam,
          item_type: typeParam,
        },
      })
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载知识资产列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize, statusFilter, typeFilter]);

  useEffect(() => {


    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState


    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。


    const timer = setTimeout(fetchItems, 0);


    return () => clearTimeout(timer);


  }, [fetchItems]);

  const columns: ColumnDef<CuratedItem>[] = [
    {
      key: "content",
      header: "内容预览",
      className: "max-w-md",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/curated/${row.id}`}
          className="text-primary hover:underline truncate block max-w-md"
        >
          {formatJsonPreview(row.content).slice(0, 80) || "(空)"}
        </Link>
      ),
    },
    {
      key: "item_type",
      header: "类型",
      render: (row) => {
        const label = TYPE_OPTIONS.find((t) => t.value === row.item_type)?.label;
        return label || row.item_type;
      },
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "updated_at",
      header: "更新时间",
      render: (row) =>
        new Date(row.updated_at).toLocaleString("zh-CN"),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Link href={`/projects/${projectId}/curated/${row.id}`}>
          <Button variant="ghost" size="xs">
            详情
          </Button>
        </Link>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">知识资产</h1>
          <p className="text-sm text-muted-foreground mt-1">
            经人工审核的正式知识资产
          </p>
        </div>
        <div className="flex items-center gap-2">
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
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={items}
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
