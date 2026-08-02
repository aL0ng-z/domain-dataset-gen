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
type ChunkSet = components["schemas"]["ChunkSetSummary"];

export default function ChunksPage() {
  const params = useParams<{ id: string; did: string }>();
  const projectId = params.id;
  const docId = params.did;
  const { page, pageSize, setPage } = usePagination();
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [chunkSets, setChunkSets] = useState<ChunkSet[]>([]);
  const [selectedSetId, setSelectedSetId] = useState<string>("active");
  const [activeSet, setActiveSet] = useState<ChunkSet | null>(null);

  // 加载版本历史，确定 active set。
  useEffect(() => {
    api
      .get("/projects/{pid}/documents/{did}/chunk-sets", {
        params: { pid: projectId, did: docId },
        query: { page: 1, page_size: 50 },
      })
      .then((data) => {
        setChunkSets(data.items);
        const active = data.items.find((cs) => cs.is_active) ?? null;
        setActiveSet(active);
      })
      .catch(() => toast.error("加载切分版本失败"));
  }, [projectId, docId]);

  const fetchChunks = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/documents/{did}/chunks", {
        params: { pid: projectId, did: docId },
        query: {
          page,
          page_size: pageSize,
          chunk_set_id: selectedSetId !== "active" ? selectedSetId : undefined,
        },
      })
      .then((data) => {
        setChunks(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载分块列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId, page, pageSize, selectedSetId]);

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

  const selectedSet = selectedSetId === "active" ? activeSet : chunkSets.find((cs) => cs.id === selectedSetId);
  // 最新版本失败时展示失败原因（旧 active set 仍保留并可查看）。
  const newestFailed = [...chunkSets].sort((a, b) => b.version - a.version).find(
    (cs) => cs.status === "failed" || cs.status === "cancelled",
  );

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
          <div className="flex items-center gap-2">
            {/* T06 §6：切换历史 set 只读查看；不得把多个 set 的同 ordinal Chunk 混在一张表。 */}
            <select
              className="rounded border px-3 py-1.5 text-sm bg-transparent"
              value={selectedSetId}
              onChange={(e) => {
                setSelectedSetId(e.target.value);
                setPage(1);
              }}
            >
              <option value="active">
                {activeSet ? `Active 版本 v${activeSet.version}` : "Active 版本（无）"}
              </option>
              {chunkSets
                .filter((cs) => cs.id !== activeSet?.id)
                .map((cs) => (
                  <option key={cs.id} value={cs.id}>
                    v{cs.version} {cs.status === "failed" ? "（失败）" : cs.status === "cancelled" ? "（已取消）" : cs.is_legacy ? "（历史）" : ""}
                  </option>
                ))}
            </select>
          </div>
        </div>
      </div>

      {/* 页头：显示 set 版本、来源 clean version、配置快照、数量、总 token 和 hash（T06 §6）。 */}
      {newestFailed && (
        <div className="mb-4 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>最新版本 v{newestFailed.version} 切分{newestFailed.status === "failed" ? "失败" : "已取消"}</span>
            {newestFailed.error_message && <span>失败原因：{newestFailed.error_message}</span>}
            <span>旧 active 版本仍保留，可继续查看。</span>
          </div>
        </div>
      )}
      {selectedSet && (
        <div className="mb-4 rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>版本：v{selectedSet.version}</span>
            {selectedSet.cleaned_document_version !== null &&
              selectedSet.cleaned_document_version !== undefined && (
                <span>来源清洗版本：v{selectedSet.cleaned_document_version}</span>
              )}
            {selectedSet.chunk_profile_name && (
              <span>配置：{selectedSet.chunk_profile_name}</span>
            )}
            <span>数量：{selectedSet.total_chunks}</span>
            <span>总 token：{selectedSet.total_tokens}</span>
            {selectedSet.output_sha256 && (
              <span className="font-mono">hash：{selectedSet.output_sha256.slice(0, 12)}…</span>
            )}
            {selectedSet.status === "failed" && selectedSet.error_message && (
              <span className="text-destructive">失败原因：{selectedSet.error_message}</span>
            )}
            {selectedSet.status === "failed" && (
              <span className="text-destructive">切分失败，旧 active 版本仍保留。</span>
            )}
          </div>
        </div>
      )}

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
