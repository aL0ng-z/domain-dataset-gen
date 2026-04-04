"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, type PaginatedResponse } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  ChevronDownIcon,
  ChevronUpIcon,
  CheckCircleIcon,
} from "lucide-react";

interface Candidate {
  id: string;
  status: string;
  content?: string;
  chunk_id?: string;
  chunk_content?: string;
  template_name?: string;
  verdict?: string;
  evidence_spans?: string;
  reject_reason?: string;
  created_at: string;
}

const VERDICT_OPTIONS = [
  { value: "supported", label: "支持" },
  { value: "partially_supported", label: "部分支持" },
  { value: "unsupported", label: "不支持" },
  { value: "out_of_scope", label: "超出范围" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "pending", label: "待审核" },
  { value: "approved", label: "已通过" },
  { value: "rejected", label: "已拒绝" },
];

export default function CandidatesPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Review form state
  const [reviewVerdict, setReviewVerdict] = useState("supported");
  const [reviewEvidence, setReviewEvidence] = useState("");
  const [reviewRejectReason, setReviewRejectReason] = useState("");

  const fetchCandidates = useCallback(() => {
    setLoading(true);
    const statusParam =
      statusFilter !== "all" ? `&status=${statusFilter}` : "";
    api
      .get<PaginatedResponse<Candidate>>(
        `/projects/${projectId}/candidates?page=${page}&page_size=${pageSize}${statusParam}`
      )
      .then((data) => {
        setCandidates(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载候选列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize, statusFilter]);

  useEffect(() => {
    fetchCandidates();
  }, [fetchCandidates]);

  const handleExpand = useCallback(
    (id: string) => {
      if (expandedId === id) {
        setExpandedId(null);
      } else {
        setExpandedId(id);
        const c = candidates.find((x) => x.id === id);
        setReviewVerdict(c?.verdict || "supported");
        setReviewEvidence(c?.evidence_spans || "");
        setReviewRejectReason(c?.reject_reason || "");
      }
    },
    [expandedId, candidates]
  );

  const handleReview = useCallback(
    async (candidateId: string, action: "approve" | "reject") => {
      try {
        await api.post(
          `/projects/${projectId}/candidates/${candidateId}/review`,
          {
            action,
            verdict: reviewVerdict,
            evidence_spans: reviewEvidence,
            reject_reason: action === "reject" ? reviewRejectReason : undefined,
          }
        );
        toast.success(action === "approve" ? "已通过" : "已拒绝");
        setExpandedId(null);
        fetchCandidates();
      } catch {
        toast.error("审核操作失败");
      }
    },
    [projectId, reviewVerdict, reviewEvidence, reviewRejectReason, fetchCandidates]
  );

  const handlePromote = useCallback(
    async (candidateId: string) => {
      try {
        await api.post(
          `/projects/${projectId}/candidates/${candidateId}/promote`
        );
        toast.success("已提升为知识资产");
        fetchCandidates();
      } catch {
        toast.error("提升失败");
      }
    },
    [projectId, fetchCandidates]
  );

  const columns: ColumnDef<Candidate>[] = [
    {
      key: "expand",
      header: "",
      className: "w-8",
      render: (row) => (
        <button onClick={() => handleExpand(row.id)}>
          {expandedId === row.id ? (
            <ChevronUpIcon className="size-4" />
          ) : (
            <ChevronDownIcon className="size-4" />
          )}
        </button>
      ),
    },
    {
      key: "content",
      header: "内容预览",
      className: "max-w-sm",
      render: (row) => (
        <span className="truncate block max-w-sm">
          {row.content?.slice(0, 100) || "(空)"}
        </span>
      ),
    },
    {
      key: "template",
      header: "模板",
      render: (row) => row.template_name || "-",
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "verdict",
      header: "判定",
      render: (row) =>
        row.verdict ? <StatusBadge status={row.verdict} /> : "-",
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
          {row.status === "approved" && (
            <Button
              variant="outline"
              size="xs"
              onClick={() => handlePromote(row.id)}
            >
              <CheckCircleIcon className="size-3" />
              提升
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
          <h1 className="text-2xl font-semibold">候选列表</h1>
          <p className="text-sm text-muted-foreground mt-1">
            审核LLM生成的候选知识
          </p>
        </div>
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
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <div>
          <DataTable
            columns={columns}
            data={candidates}
            total={total}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            rowKey={(row) => row.id}
          />

          {/* Expanded review panel */}
          {expandedId && (
            <Card className="mt-4">
              <CardContent className="p-4">
                {(() => {
                  const c = candidates.find((x) => x.id === expandedId);
                  if (!c) return null;
                  return (
                    <div className="space-y-4">
                      <div>
                        <div className="text-sm font-medium mb-1">
                          候选内容
                        </div>
                        <pre className="whitespace-pre-wrap text-sm font-mono bg-muted/50 rounded p-3 max-h-64 overflow-auto">
                          {c.content || "(空)"}
                        </pre>
                      </div>
                      {c.chunk_content && (
                        <div>
                          <div className="text-sm font-medium mb-1">
                            源分块
                          </div>
                          <pre className="whitespace-pre-wrap text-xs font-mono bg-blue-50 dark:bg-blue-950 rounded p-3 max-h-40 overflow-auto">
                            {c.chunk_content}
                          </pre>
                        </div>
                      )}
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="text-sm font-medium">
                            判定
                          </label>
                          <select
                            className="w-full rounded border px-3 py-1.5 text-sm bg-transparent mt-1"
                            value={reviewVerdict}
                            onChange={(e) =>
                              setReviewVerdict(e.target.value)
                            }
                          >
                            {VERDICT_OPTIONS.map((v) => (
                              <option key={v.value} value={v.value}>
                                {v.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label className="text-sm font-medium">
                            证据引用
                          </label>
                          <Input
                            value={reviewEvidence}
                            onChange={(e) =>
                              setReviewEvidence(e.target.value)
                            }
                            placeholder="原文引用片段"
                            className="mt-1"
                          />
                        </div>
                      </div>
                      <div>
                        <label className="text-sm font-medium">
                          拒绝原因
                        </label>
                        <Textarea
                          value={reviewRejectReason}
                          onChange={(e) =>
                            setReviewRejectReason(e.target.value)
                          }
                          placeholder="如拒绝请填写原因"
                          className="mt-1"
                        />
                      </div>
                      <div className="flex gap-2">
                        <Button
                          onClick={() =>
                            handleReview(expandedId, "approve")
                          }
                        >
                          通过
                        </Button>
                        <Button
                          variant="destructive"
                          onClick={() =>
                            handleReview(expandedId, "reject")
                          }
                        >
                          拒绝
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => setExpandedId(null)}
                        >
                          取消
                        </Button>
                      </div>
                    </div>
                  );
                })()}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
