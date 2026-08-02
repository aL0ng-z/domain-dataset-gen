"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { ArrowLeftIcon } from "lucide-react";

type Chunk = components["schemas"]["ChunkResponse"];
type Section = components["schemas"]["SectionResponse"];

export default function ChunksPage() {
  const params = useParams<{ id: string; did: string }>();
  const projectId = params.id;
  const docId = params.did;
  const { page, pageSize, setPage } = usePagination();
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sections, setSections] = useState<Section[]>([]);
  const [sectionFilter, setSectionFilter] = useState<string>("all");

  useEffect(() => {
    api
      .get("/projects/{pid}/documents/{did}/sections", {
        params: { pid: projectId, did: docId },
        query: { page: 1, page_size: 100 },
      })
      .then((data) => setSections(data.items))
      .catch(() => {});
  }, [projectId, docId]);

  const fetchChunks = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/documents/{did}/chunks", {
        params: { pid: projectId, did: docId },
        query: {
          page,
          page_size: pageSize,
          section_id: sectionFilter !== "all" ? sectionFilter : undefined,
        },
      })
      .then((data) => {
        setChunks(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载分块列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId, page, pageSize, sectionFilter]);

  useEffect(() => {


    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState


    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。


    const timer = setTimeout(fetchChunks, 0);


    return () => clearTimeout(timer);


  }, [fetchChunks]);

  const columns: ColumnDef<Chunk>[] = [
    {
      key: "index",
      header: "序号",
      render: (row) => row.ordinal + 1,
    },
    {
      key: "section",
      header: "所属章节",
      render: (row) => row.heading_path || "-",
    },
    {
      key: "content",
      header: "内容预览",
      className: "max-w-xs",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/documents/${docId}/chunks/${row.id}`}
          className="text-primary hover:underline truncate block max-w-xs"
        >
          {row.content?.slice(0, 80) || "(空)"}
        </Link>
      ),
    },
    {
      key: "token_count",
      header: "Token数",
      render: (row) => String(row.token_count),
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/documents/${docId}/chunks/${row.id}`}
        >
          <Button variant="ghost" size="xs">
            详情
          </Button>
        </Link>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/documents/${docId}`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回文档详情
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">分块列表</h1>
            <p className="text-sm text-muted-foreground mt-1">
              共 {total} 个分块
            </p>
          </div>
          <div>
            <select
              className="rounded border px-3 py-1.5 text-sm bg-transparent"
              value={sectionFilter}
              onChange={(e) => {
                setSectionFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="all">全部章节</option>
              {sections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.ordinal + 1}. {s.heading_path || "无标题"}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={chunks}
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
