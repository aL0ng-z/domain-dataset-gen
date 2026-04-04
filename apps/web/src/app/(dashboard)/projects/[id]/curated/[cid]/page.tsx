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
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import {
  ArrowLeftIcon,
  SaveIcon,
  Loader2Icon,
  HistoryIcon,
} from "lucide-react";

interface CuratedItemDetail {
  id: string;
  item_type: string;
  status: string;
  content: string;
  heading_path?: string;
  version: number;
  evidence_links?: EvidenceLink[];
  revision_history?: Revision[];
  created_at: string;
  updated_at: string;
}

interface EvidenceLink {
  document_id?: string;
  document_name?: string;
  chunk_id?: string;
  pages?: string;
  heading_path?: string;
  quote_text?: string;
}

interface Revision {
  version: number;
  content_preview?: string;
  changed_by?: string;
  changed_at: string;
}

export default function CuratedItemDetailPage() {
  const params = useParams<{ id: string; cid: string }>();
  const projectId = params.id;
  const itemId = params.cid;

  const [item, setItem] = useState<CuratedItemDetail | null>(null);
  const [editContent, setEditContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const fetchItem = useCallback(() => {
    api
      .get<CuratedItemDetail>(
        `/projects/${projectId}/curated-items/${itemId}`
      )
      .then((data) => {
        setItem(data);
        setEditContent(data.content || "");
      })
      .catch(() => toast.error("加载知识资产详情失败"))
      .finally(() => setLoading(false));
  }, [projectId, itemId]);

  useEffect(() => {
    fetchItem();
  }, [fetchItem]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await api.put(
        `/projects/${projectId}/curated-items/${itemId}`,
        { content: editContent }
      );
      toast.success("保存成功");
      fetchItem();
    } catch {
      toast.error("保存失败");
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
                v{item.version}
              </span>
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
              <CardTitle>内容</CardTitle>
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
              {item.evidence_links && item.evidence_links.length > 0 ? (
                <div className="space-y-3">
                  {item.evidence_links.map((link, idx) => (
                    <div
                      key={idx}
                      className="border rounded-lg p-3 text-sm"
                    >
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <span className="text-muted-foreground">
                            文档:{" "}
                          </span>
                          {link.document_name || link.document_id || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            页码:{" "}
                          </span>
                          {link.pages || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            标题路径:{" "}
                          </span>
                          {link.heading_path || "-"}
                        </div>
                        <div>
                          <span className="text-muted-foreground">
                            分块ID:{" "}
                          </span>
                          {link.chunk_id || "-"}
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
                {item.revision_history &&
                item.revision_history.length > 0 ? (
                  <div className="space-y-2">
                    {item.revision_history.map((rev) => (
                      <div
                        key={rev.version}
                        className="border rounded p-2 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium">
                            v{rev.version}
                          </span>
                          <span className="text-muted-foreground">
                            {new Date(
                              rev.changed_at
                            ).toLocaleString("zh-CN")}
                          </span>
                        </div>
                        {rev.changed_by && (
                          <div className="text-muted-foreground mt-0.5">
                            修改者: {rev.changed_by}
                          </div>
                        )}
                        {rev.content_preview && (
                          <div className="mt-1 truncate">
                            {rev.content_preview}
                          </div>
                        )}
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
                <span className="text-muted-foreground">标题路径: </span>
                {item.heading_path || "-"}
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
