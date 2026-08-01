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
import { api, formatJsonPreview, parseJsonObject } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import {
  ArrowLeftIcon,
  SaveIcon,
  Loader2Icon,
  HistoryIcon,
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
  const [evidenceLinks, setEvidenceLinks] = useState<EvidenceLink[]>([]);
  const [revisions, setRevisions] = useState<CuratedRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const fetchItem = useCallback(() => {
    const pid = { pid: projectId, iid: itemId };
    Promise.all([
      api.get("/projects/{pid}/curated-items/{iid}", { params: pid }),
      api.get("/projects/{pid}/curated-items/{iid}/evidence-links", { params: pid }).catch(() => [] as EvidenceLink[]),
      api.get("/projects/{pid}/curated-items/{iid}/revisions", { params: pid }).catch(() => [] as CuratedRevision[]),
    ])
      .then(([data, links, revs]) => {
        setItem(data);
        setEditContent(formatJsonPreview(data.content));
        setEvidenceLinks(links);
        setRevisions(revs);
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
    setSaving(true);
    try {
      // 请求前解析校验：确认编辑内容为 JSON object。
      const parsed = parseJsonObject(editContent);
      await api.patch(
        "/projects/{pid}/curated-items/{iid}",
        { content: parsed },
        { params: { pid: projectId, iid: itemId } },
      );
      toast.success("保存成功");
      fetchItem();
    } catch {
      toast.error("保存失败（内容必须是 JSON 对象）");
    } finally {
      setSaving(false);
    }
  }, [projectId, itemId, editContent, fetchItem]);

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
            <Button onClick={handleSave} disabled={saving}>
              {saving ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <SaveIcon className="size-4" />
              )}
              保存
            </Button>
          </div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Content editor (2 cols) */}
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>内容 (JSON)</CardTitle>
            </CardHeader>
            <CardContent>
              <Textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="min-h-64 font-mono text-sm"
              />
            </CardContent>
          </Card>

          {/* Evidence links */}
          <Card>
            <CardHeader>
              <CardTitle>证据链接</CardTitle>
              <CardDescription>
                追溯到原始文档、分块和页面的证据
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
                            标题路径:{" "}
                          </span>
                          {link.heading_path || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            页码:{" "}
                          </span>
                          {link.source_pages ? formatJsonPreview(link.source_pages) : "-"}
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
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">
                  暂无证据链接
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Sidebar: revision history */}
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
                        className="border rounded p-2 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono">
                            {rev.id.slice(0, 8)}
                          </span>
                          <span className="text-muted-foreground">
                            {new Date(rev.created_at).toLocaleString("zh-CN")}
                          </span>
                        </div>
                        {rev.revision_note && (
                          <div className="text-muted-foreground mt-0.5">
                            备注: {rev.revision_note}
                          </div>
                        )}
                        <div className="mt-1 truncate">
                          {formatJsonPreview(rev.content)}
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
              <CardTitle>元信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div>
                <span className="text-muted-foreground">状态: </span>
                {item.status}
              </div>
              <div>
                <span className="text-muted-foreground">候选 ID: </span>
                <span className="font-mono text-xs">{item.candidate_id}</span>
              </div>
              <div>
                <span className="text-muted-foreground">创建时间: </span>
                {new Date(item.created_at).toLocaleString("zh-CN")}
              </div>
              <div>
                <span className="text-muted-foreground">更新时间: </span>
                {new Date(item.updated_at).toLocaleString("zh-CN")}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
