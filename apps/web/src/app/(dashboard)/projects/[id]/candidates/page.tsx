"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import {
  api,
  formatJsonPreview,
  parseJsonObject,
  type ApiErrorException,
} from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { Textarea } from "@/components/ui/textarea";
import {
  ChevronDownIcon,
  ChevronUpIcon,
  CheckCircleIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";

type Candidate = components["schemas"]["CandidateResponse"];
type EvidenceSpan = components["schemas"]["EvidenceSpan"];

const VERDICT_OPTIONS = [
  { value: "supported", label: "支持" },
  { value: "partially_supported", label: "部分支持" },
  { value: "unsupported", label: "不支持" },
  { value: "out_of_scope", label: "超出范围" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "ai_generated", label: "AI 生成" },
  { value: "human_edited", label: "人工编辑" },
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
  const [reviewSpans, setReviewSpans] = useState<EvidenceSpan[]>([]);
  const [reviewRejectReason, setReviewRejectReason] = useState("");

  // JSON editor state
  const [editContent, setEditContent] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  // Evidence selector state
  const [sourceChunk, setSourceChunk] = useState<string>("");
  const [chunkContent, setChunkContent] = useState<string>("");
  const [selectedText, setSelectedText] = useState("");
  const [quoteStart, setQuoteStart] = useState(0);
  const [quoteEnd, setQuoteEnd] = useState(0);

  const fetchCandidates = useCallback(() => {
    setLoading(true);
    const statusParam =
      statusFilter !== "all" ? statusFilter : undefined;
    api
      .get("/candidates", {
        query: {
          project_id: projectId,
          page,
          page_size: pageSize,
          status: statusParam,
        },
      })
      .then((data) => {
        setCandidates(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载候选列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize, statusFilter]);

  useEffect(() => {
    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState
    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。
    const timer = setTimeout(fetchCandidates, 0);

    return () => clearTimeout(timer);
  }, [fetchCandidates]);

  const handleExpand = useCallback(
    (id: string) => {
      if (expandedId === id) {
        setExpandedId(null);
        return;
      }
      setExpandedId(id);
      const c = candidates.find((x) => x.id === id);
      setReviewVerdict(c?.review_verdict || "supported");
      setReviewSpans(
        Array.isArray(c?.review_evidence_spans?.spans)
          ? (c.review_evidence_spans.spans as EvidenceSpan[])
          : [],
      );
      setReviewRejectReason(c?.reject_reason || "");
      setEditContent(c ? formatJsonPreview(c.content) : "");
      setEditError(null);
      setSourceChunk(c?.chunk_id || "");
      setSelectedText("");
      // 加载源 Chunk 原文供证据选择（后端仍是权威）。
      if (c?.chunk_id) {
        api
          .get("/chunks/{cid}", { params: { cid: c.chunk_id } })
          .then((chunk) => {
            setChunkContent(chunk.content || "");
            setSourceChunk(chunk.id);
          })
          .catch(() => setChunkContent(""));
      } else {
        setChunkContent("");
      }
    },
    [expandedId, candidates],
  );

  const handleReview = useCallback(
    async (candidateId: string, verdict: string) => {
      try {
        const evidence_spans =
          verdict === "supported" || verdict === "partially_supported"
            ? reviewSpans.map((s) => ({
                chunk_id: s.chunk_id,
                start_char: s.start_char,
                end_char: s.end_char,
                quote_text: s.quote_text,
              }))
            : null;
        const reject_reason =
          verdict === "unsupported" || verdict === "out_of_scope"
            ? reviewRejectReason || null
            : null;
        await api.post(
          "/candidates/{cid}/review",
          {
            verdict,
            evidence_spans,
            reject_reason,
          },
          { params: { cid: candidateId } },
        );
        toast.success(verdict === "supported" || verdict === "partially_supported" ? "已通过" : "已拒绝");
        setExpandedId(null);
        fetchCandidates();
      } catch (err) {
        const e = err as ApiErrorException;
        if (e?.apiError?.code === "CANDIDATE_EVIDENCE_REQUIRED") {
          toast.error("支持判定必须提供证据");
        } else if (e?.apiError?.code === "CANDIDATE_ALREADY_PROMOTED") {
          toast.error("该候选已提升，审核结论不可修改");
        } else if (e?.apiError?.kind === "validation") {
          toast.error("证据或拒绝原因校验失败，请检查");
        } else {
          toast.error("审核操作失败");
        }
      }
    },
    [reviewSpans, reviewRejectReason, fetchCandidates],
  );

  const handleSaveEdit = useCallback(
    async (candidateId: string) => {
      try {
        const parsed = parseJsonObject(editContent);
        await api.patch("/candidates/{cid}", { content: parsed }, { params: { cid: candidateId } });
        toast.success("已保存（状态转为人工编辑）");
        setExpandedId(null);
        fetchCandidates();
      } catch (err) {
        const e = err as ApiErrorException;
        if (e?.apiError?.code === "CANDIDATE_ALREADY_PROMOTED") {
          toast.error("该候选已提升，内容不可修改");
        } else if (e?.apiError?.kind === "validation") {
          toast.error("内容必须是 JSON 对象");
        } else {
          toast.error("保存失败");
        }
      }
    },
    [editContent, fetchCandidates],
  );

  const handlePromote = useCallback(
    async (candidateId: string) => {
      try {
        await api.post("/candidates/{cid}/promote-to-curated", undefined, {
          params: { cid: candidateId },
        });
        toast.success("已提升为知识资产");
        fetchCandidates();
      } catch (err) {
        const e = err as ApiErrorException;
        if (e?.apiError?.code === "CANDIDATE_ALREADY_PROMOTED") {
          toast.error("该候选已提升");
        } else if (e?.apiError?.code === "CANDIDATE_EVIDENCE_REQUIRED") {
          toast.error("缺少有效证据，无法提升");
        } else {
          toast.error("提升失败");
        }
      }
    },
    [fetchCandidates],
  );

  /** 文本选中 -> 生成 Unicode code point offset（前端 helper 显式换算，后端仍是权威）。 */
  const handleSelectEvidence = useCallback(
    (chunkContent: string) => {
      const textarea = document.getElementById("chunk-text") as HTMLTextAreaElement | null;
      if (!textarea) return;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      if (start === end) return;
      // UTF-16 -> Unicode code point：surrogate pair 算一个 code point。
      const countBefore = (s: number) =>
        Array.from(chunkContent.slice(0, s)).length;
      const countEnd = (s: number) =>
        Array.from(chunkContent.slice(0, s)).length;
      const cpStart = countBefore(start);
      const cpEnd = countEnd(end);
      const quote = chunkContent.slice(start, end);
      setSelectedText(quote);
      setQuoteStart(cpStart);
      setQuoteEnd(cpEnd);
    },
    [],
  );

  const addSpan = useCallback(() => {
    if (!sourceChunk || !selectedText || quoteEnd <= quoteStart) {
      toast.error("请先在原文中选中证据文本");
      return;
    }
    setReviewSpans((prev) => {
      const dup = prev.some(
        (s) =>
          s.chunk_id === sourceChunk &&
          s.start_char === quoteStart &&
          s.end_char === quoteEnd,
      );
      if (dup) return prev;
      return [
        ...prev,
        {
          chunk_id: sourceChunk,
          start_char: quoteStart,
          end_char: quoteEnd,
          quote_text: selectedText,
        },
      ];
    });
    setSelectedText("");
  }, [sourceChunk, selectedText, quoteStart, quoteEnd]);

  const removeSpan = useCallback(
    (idx: number) => {
      setReviewSpans((prev) => prev.filter((_, i) => i !== idx));
    },
    [],
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
          {formatJsonPreview(row.content).slice(0, 100) || "(空)"}
        </span>
      ),
    },
    {
      key: "candidate_type",
      header: "类型",
      render: (row) => row.candidate_type || "-",
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "review_verdict",
      header: "判定",
      render: (row) =>
        row.review_verdict ? <StatusBadge status={row.review_verdict} /> : "-",
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) => new Date(row.created_at).toLocaleString("zh-CN"),
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
            审核LLM生成的候选知识，先编辑 JSON 再核对证据
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

          {expandedId && (
            <Card className="mt-4">
              <CardContent className="p-4">
                {(() => {
                  const c = candidates.find((x) => x.id === expandedId);
                  if (!c) return null;
                  return (
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <div className="text-sm font-medium">候选审核面板</div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <StatusBadge status={c.status} />
                        </div>
                      </div>

                      {/* JSON 编辑器：解析失败留在本地并显示行列，不发 PATCH */}
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <div className="text-sm font-medium">内容 (JSON 对象)</div>
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => handleSaveEdit(c.id)}
                          >
                            保存编辑
                          </Button>
                        </div>
                        <Textarea
                          value={editContent}
                          onChange={(e) => {
                            setEditContent(e.target.value);
                            try {
                              parseJsonObject(e.target.value);
                              setEditError(null);
                            } catch (err) {
                              setEditError(
                                err instanceof Error ? err.message : "JSON 解析失败",
                              );
                            }
                          }}
                          className="min-h-40 font-mono text-sm"
                          data-testid="candidate-json-editor"
                        />
                        {editError && (
                          <div className="mt-1 text-xs text-destructive">
                            {editError}
                          </div>
                        )}
                      </div>

                      {/* 证据选择器 */}
                      <div>
                        <div className="text-sm font-medium mb-1">
                          证据选择（选中原文生成 span）
                        </div>
                        <Textarea
                          id="chunk-text"
                          readOnly
                          value={chunkContent}
                          className="min-h-24 font-mono text-xs bg-muted/50"
                          data-testid="chunk-text"
                          onSelect={() => handleSelectEvidence(chunkContent)}
                        />
                        <div className="flex items-center gap-2 mt-2">
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={addSpan}
                            data-testid="add-span"
                          >
                            <PlusIcon className="size-3" />
                            添加证据 span
                          </Button>
                          {selectedText && (
                            <span className="text-xs text-muted-foreground">
                              选中 [{quoteStart}, {quoteEnd}): {selectedText.slice(0, 40)}
                            </span>
                          )}
                        </div>
                        {reviewSpans.length > 0 && (
                          <div className="mt-2 space-y-1">
                            {reviewSpans.map((s, idx) => (
                              <div
                                key={`${s.chunk_id}-${s.start_char}-${s.end_char}`}
                                className="flex items-center justify-between border rounded px-2 py-1 text-xs"
                              >
                                <span className="truncate">
                                  [{s.start_char},{s.end_char}) {s.quote_text}
                                </span>
                                <button onClick={() => removeSpan(idx)}>
                                  <Trash2Icon className="size-3 text-muted-foreground" />
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="text-sm font-medium">判定</label>
                          <select
                            className="w-full rounded border px-3 py-1.5 text-sm bg-transparent mt-1"
                            value={reviewVerdict}
                            onChange={(e) => setReviewVerdict(e.target.value)}
                          >
                            {VERDICT_OPTIONS.map((v) => (
                              <option key={v.value} value={v.value}>
                                {v.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                      {(reviewVerdict === "unsupported" ||
                        reviewVerdict === "out_of_scope") && (
                        <div>
                          <label className="text-sm font-medium">拒绝原因</label>
                          <Textarea
                            value={reviewRejectReason}
                            onChange={(e) => setReviewRejectReason(e.target.value)}
                            placeholder="拒绝必须填写原因"
                            className="mt-1"
                          />
                        </div>
                      )}
                      <div className="flex gap-2">
                        <Button
                          onClick={() => handleReview(expandedId, reviewVerdict)}
                          data-testid="submit-review"
                        >
                          {reviewVerdict === "unsupported" ||
                          reviewVerdict === "out_of_scope"
                            ? "拒绝"
                            : "通过"}
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
