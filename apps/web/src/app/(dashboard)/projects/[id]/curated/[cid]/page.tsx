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
import { StatusBadge } from "@/components/status-badge";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  formatJsonPreview,
  parseJsonObject,
  type ApiErrorException,
} from "@/lib/api";
import type { components } from "@/lib/api/generated";
import {
  ArrowLeftIcon,
  SaveIcon,
  Loader2Icon,
  HistoryIcon,
  CheckCircleIcon,
  RotateCcwIcon,
} from "lucide-react";

type CuratedItemDetail = components["schemas"]["CuratedItemResponse"];
type EvidenceLink = components["schemas"]["EvidenceLinkResponse"];
type CuratedRevision = components["schemas"]["CuratedRevisionResponse"];

export default function CuratedItemDetailPage() {
  const params = useParams<{ id: string; cid: string }>();
  const projectId = params.id;
  const itemId = params.cid;

  const [item, setItem] = useState<CuratedItemDetail | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [evidenceLinks, setEvidenceLinks] = useState<EvidenceLink[]>([]);
  const [revisions, setRevisions] = useState<CuratedRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [serverRevision, setServerRevision] = useState<number | null>(null);
  const [isReviewer, setIsReviewer] = useState(false);

  const fetchItem = useCallback(() => {
    const pid = { pid: projectId, iid: itemId };
    Promise.all([
      api.get("/projects/{pid}/curated-items/{iid}", { params: pid }),
      api
        .get("/projects/{pid}/curated-items/{iid}/evidence", {
          params: pid,
          query: { page: 1, page_size: 20 },
        })
        .catch(() => ({ items: [] as EvidenceLink[], total: 0 })),
      api
        .get("/projects/{pid}/curated-items/{iid}/revisions", {
          params: pid,
          query: { page: 1, page_size: 20 },
        })
        .catch(() => ({ items: [] as CuratedRevision[], total: 0 })),
      api
        .get("/auth/me")
        .then((u) => (u as { role?: string }).role)
        .catch(() => undefined),
    ])
      .then(([data, links, revs, globalRole]) => {
        setItem(data);
        setEditContent(formatJsonPreview(data.content));
        setServerRevision(data.current_revision);
        setEvidenceLinks(links.items);
        setRevisions(revs.items);
        // reviewer 以上（全局角色）可见审批控件；editor 不展示（后端仍权威）。
        setIsReviewer(globalRole === "reviewer" || globalRole === "admin");
      })
      .catch(() => toast.error("加载知识资产详情失败"))
      .finally(() => setLoading(false));
  }, [projectId, itemId]);

  useEffect(() => {
    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState
    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。
    const timer = setTimeout(fetchItem, 0);

    return () => clearTimeout(timer);
  }, [fetchItem]);

  const handleSave = useCallback(async () => {
    if (!item) return;
    setSaving(true);
    try {
      // 请求前解析校验：确认编辑内容为 JSON object。
      const parsed = parseJsonObject(editContent);
      await api.patch(
        "/projects/{pid}/curated-items/{iid}",
        {
          content: parsed,
          revision_note: "修正文案",
          expected_revision: item.current_revision,
        },
        { params: { pid: projectId, iid: itemId } },
      );
      toast.success("保存成功");
      fetchItem();
    } catch (err) {
      const e = err as ApiErrorException;
      if (e?.apiError?.code === "CURATED_REVISION_CONFLICT") {
        // 409 并发冲突：保留本地草稿、展示服务端当前 revision，提供重新加载。
        const current =
          (e.apiError.context as { current_revision?: number } | undefined)
            ?.current_revision;
        setServerRevision(current ?? null);
        toast.error(
          `版本冲突：服务端当前为 v${current ?? "?"}，已保留本地草稿`,
        );
      } else if (e?.apiError?.code === "CURATED_REVIEW_STATE_CONFLICT") {
        toast.error("仅草稿条目可编辑；审批状态请先退审");
      } else if (e?.apiError?.kind === "validation") {
        toast.error("内容必须是 JSON 对象");
      } else {
        toast.error("保存失败");
      }
    } finally {
      setSaving(false);
    }
  }, [projectId, itemId, editContent, item, fetchItem]);

  const handleApprove = useCallback(async () => {
    if (!item) return;
    try {
      await api.post(
        "/projects/{pid}/curated-items/{iid}/review",
        {
          action: "approve",
          reason: null,
          expected_revision: item.current_revision,
        },
        { params: { pid: projectId, iid: itemId } },
      );
      toast.success("已批准");
      fetchItem();
    } catch (err) {
      const e = err as ApiErrorException;
      if (e?.apiError?.code === "CURATED_APPROVAL_GATE_FAILED") {
        toast.error("审批门禁不满足（证据/源候选/内容）");
      } else if (e?.apiError?.code === "CURATED_REVISION_CONFLICT") {
        toast.error("版本冲突，请重新加载");
      } else if (e?.apiError?.code === "CURATED_REVIEW_STATE_CONFLICT") {
        toast.error("当前状态不允许批准");
      } else {
        toast.error("审批失败");
      }
    }
  }, [projectId, itemId, item, fetchItem]);

  const handleNeedsRevision = useCallback(async () => {
    if (!item) return;
    const reason = window.prompt("请输入退审原因：");
    if (!reason) return;
    try {
      await api.post(
        "/projects/{pid}/curated-items/{iid}/review",
        {
          action: "needs_revision",
          reason,
          expected_revision: item.current_revision,
        },
        { params: { pid: projectId, iid: itemId } },
      );
      toast.success("已退回草稿");
      fetchItem();
    } catch (err) {
      const e = err as ApiErrorException;
      if (e?.apiError?.code === "CURATED_REVISION_CONFLICT") {
        toast.error("版本冲突，请重新加载");
      } else if (e?.apiError?.code === "CURATED_REVIEW_STATE_CONFLICT") {
        toast.error("仅已批准条目可退审");
      } else {
        toast.error("退审失败");
      }
    }
  }, [projectId, itemId, item, fetchItem]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          知识资产不存在
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/curated`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回知识资产列表
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">知识资产详情</h1>
            <div className="flex items-center gap-2 mt-1">
              <StatusBadge status={item.status} />
              <span className="text-sm text-muted-foreground">
                {item.item_type}
              </span>
              <span className="text-sm text-muted-foreground">
                v{item.current_revision}
              </span>
              {serverRevision !== null &&
                serverRevision !== item.current_revision && (
                  <span className="text-xs text-destructive">
                    服务端已更新到 v{serverRevision}
                  </span>
                )}
            </div>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => setShowHistory(!showHistory)}
            >
              <HistoryIcon className="size-4" />
              修订历史
            </Button>
            {item.status === "draft" && (
              <Button onClick={handleSave} disabled={saving}>
                {saving ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <SaveIcon className="size-4" />
                )}
                保存
              </Button>
            )}
            {isReviewer && item.status === "draft" && (
              <Button onClick={handleApprove}>
                <CheckCircleIcon className="size-4" />
                批准
              </Button>
            )}
            {isReviewer && item.status === "approved" && (
              <Button variant="destructive" onClick={handleNeedsRevision}>
                <RotateCcwIcon className="size-4" />
                退审
              </Button>
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Content editor (2 cols) */}
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>内容 (JSON)</CardTitle>
              <CardDescription>
                {item.status === "approved"
                  ? "已批准条目不可编辑；如需修改请先退审"
                  : "修改保存将生成新 revision（v" +
                    (item.current_revision + 1) +
                    "）"}
              </CardDescription>
            </CardHeader>
            <CardContent>
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
                readOnly={item.status === "approved"}
                className="min-h-64 font-mono text-sm"
                data-testid="curated-json-editor"
              />
              {editError && (
                <div className="mt-1 text-xs text-destructive">{editError}</div>
              )}
            </CardContent>
          </Card>

          {/* Evidence links */}
          <Card>
            <CardHeader>
              <CardTitle>证据链接</CardTitle>
              <CardDescription>
                追溯到原始文档、分块、精确字符范围与 quote 的证据
              </CardDescription>
            </CardHeader>
            <CardContent>
              {evidenceLinks.length > 0 ? (
                <div className="space-y-3">
                  {evidenceLinks.map((link) => (
                    <div
                      key={link.id}
                      className="border rounded-lg p-3 text-sm"
                    >
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <span className="text-muted-foreground">
                            文档 ID:{" "}
                          </span>
                          {link.document_id || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            分块 ID:{" "}
                          </span>
                          {link.chunk_id || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            字符范围:{" "}
                          </span>
                          [{link.start_char},{link.end_char})
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            标题路径:{" "}
                          </span>
                          {link.heading_path || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            页码:{" "}
                          </span>
                          {link.source_pages
                            ? formatJsonPreview(link.source_pages)
                            : "-"}
                        </div>
                      </div>
                      {link.quote_text && (
                        <div className="mt-2 bg-muted/50 rounded p-2 text-xs">
                          <span className="text-muted-foreground">
                            引用:{" "}
                          </span>
                          {link.quote_text}
                        </div>
                      )}
                    </div>
                  ))}
                  {evidenceLinks.length >= 20 && (
                    <div className="text-xs text-muted-foreground">
                      证据较多，仅显示第一页
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">
                  暂无证据链接
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Sidebar: revision history + approval info */}
        <div>
          {showHistory && (
            <Card>
              <CardHeader>
                <CardTitle>修订历史</CardTitle>
              </CardHeader>
              <CardContent>
                {revisions.length > 0 ? (
                  <div className="space-y-2">
                    {revisions.map((rev) => (
                      <div
                        key={rev.id}
                        className={`border rounded p-2 text-xs ${
                          item.approved_revision_id === rev.id
                            ? "border-green-500"
                            : ""
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono">v{rev.version}</span>
                          <span className="text-muted-foreground">
                            {new Date(rev.created_at).toLocaleString("zh-CN")}
                          </span>
                        </div>
                        {item.approved_revision_id === rev.id && (
                          <div className="text-xs text-green-600">
                            当前已批准修订
                          </div>
                        )}
                        {rev.revision_note && (
                          <div className="text-muted-foreground mt-0.5">
                            备注: {rev.revision_note}
                          </div>
                        )}
                        <div className="mt-1 font-mono text-[10px] truncate">
                          sha256: {rev.content_sha256?.slice(0, 16)}…
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">
                    暂无修订记录
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          <Card className={showHistory ? "mt-4" : ""}>
            <CardHeader>
              <CardTitle>审批信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div>
                <span className="text-muted-foreground">状态: </span>
                <StatusBadge status={item.status} />
              </div>
              <div>
                <span className="text-muted-foreground">当前版本: </span>
                v{item.current_revision}
              </div>
              {item.approved_revision_id && (
                <div>
                  <span className="text-muted-foreground">已批准修订: </span>
                  <span className="font-mono text-xs">
                    {item.approved_revision_id.slice(0, 8)}
                  </span>
                </div>
              )}
              {item.approved_at && (
                <div>
                  <span className="text-muted-foreground">批准时间: </span>
                  {new Date(item.approved_at).toLocaleString("zh-CN")}
                </div>
              )}
              <div>
                <span className="text-muted-foreground">候选 ID: </span>
                <span className="font-mono text-xs">{item.candidate_id}</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
