"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StatusBadge } from "@/components/status-badge";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  LockIcon,
  SaveIcon,
  SendIcon,
  CheckIcon,
  XIcon,
  MessageSquareIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
} from "lucide-react";

// Dynamically import CodeMirror to avoid SSR issues
const CodeMirrorEditor = dynamic(
  () => import("./codemirror-editor").then((m) => ({ default: m.CodeMirrorEditor })),
  { ssr: false, loading: () => <div className="p-4 text-sm text-muted-foreground">编辑器加载中...</div> }
);

interface Section {
  id: string;
  title: string;
  section_index: number;
  status: string;
  locked_by?: string;
  locked_by_name?: string;
  raw_markdown?: string;
  cleaned_markdown?: string;
}

interface Comment {
  id: string;
  content: string;
  author_name?: string;
  created_at: string;
}

export default function CleaningWorkbenchPage() {
  const params = useParams<{ id: string; did: string }>();
  const projectId = params.id;
  const docId = params.did;

  const [sections, setSections] = useState<Section[]>([]);
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null);
  const [selectedSection, setSelectedSection] = useState<Section | null>(null);
  const [editedMarkdown, setEditedMarkdown] = useState("");
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [pdfUrl, setPdfUrl] = useState<string>("");

  // Fetch sections list
  const fetchSections = useCallback(() => {
    api
      .get<{ items: Section[] }>(
        `/projects/${projectId}/documents/${docId}/sections?page=1&page_size=200`
      )
      .then((data) => {
        setSections(data.items);
        if (!selectedSectionId && data.items.length > 0) {
          setSelectedSectionId(data.items[0].id);
        }
      })
      .catch(() => toast.error("加载章节列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId, selectedSectionId]);

  useEffect(() => {
    fetchSections();
    // Fetch PDF URL
    setPdfUrl(`/api/projects/${projectId}/documents/${docId}/file`);
  }, [projectId, docId, fetchSections]);

  // Fetch selected section detail
  useEffect(() => {
    if (!selectedSectionId) return;
    api
      .get<Section>(
        `/sections/${selectedSectionId}`
      )
      .then((section) => {
        setSelectedSection(section);
        setEditedMarkdown(section.cleaned_markdown || section.raw_markdown || "");
      })
      .catch(() => toast.error("加载章节详情失败"));

    // Fetch comments
    api
      .get<{ items: Comment[] }>(
        `/sections/${selectedSectionId}/comments?page=1&page_size=50`
      )
      .then((data) => setComments(data.items))
      .catch(() => setComments([]));
  }, [projectId, docId, selectedSectionId]);

  // Heartbeat for lease
  useEffect(() => {
    if (!selectedSectionId) return;
    const interval = setInterval(() => {
      api
        .post(
          `/sections/${selectedSectionId}/lease/heartbeat`
        )
        .catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, [projectId, docId, selectedSectionId]);

  const handleSave = useCallback(async () => {
    if (!selectedSectionId) return;
    setSaving(true);
    try {
      await api.patch(
        `/sections/${selectedSectionId}`,
        { cleaned_markdown: editedMarkdown }
      );
      toast.success("保存成功");
      fetchSections();
    } catch {
      toast.error("保存失败");
    } finally {
      setSaving(false);
    }
  }, [projectId, docId, selectedSectionId, editedMarkdown, fetchSections]);

  const handleSubmitReview = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(
        `/sections/${selectedSectionId}/submit`
      );
      toast.success("已提交审核");
      fetchSections();
    } catch {
      toast.error("提交审核失败");
    }
  }, [projectId, docId, selectedSectionId, fetchSections]);

  const handleApprove = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(
        `/sections/${selectedSectionId}/review`,
        { action: "accept" }
      );
      toast.success("已通过");
      fetchSections();
    } catch {
      toast.error("操作失败");
    }
  }, [projectId, docId, selectedSectionId, fetchSections]);

  const handleReject = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(
        `/sections/${selectedSectionId}/review`,
        { action: "reject" }
      );
      toast.success("已驳回");
      fetchSections();
    } catch {
      toast.error("操作失败");
    }
  }, [projectId, docId, selectedSectionId, fetchSections]);

  const handleAddComment = useCallback(async () => {
    if (!selectedSectionId || !newComment.trim()) return;
    try {
      await api.post(
        `/sections/${selectedSectionId}/comments`,
        { content: newComment }
      );
      setNewComment("");
      // Refetch comments
      const data = await api.get<{ items: Comment[] }>(
        `/sections/${selectedSectionId}/comments?page=1&page_size=50`
      );
      setComments(data.items);
    } catch {
      toast.error("评论失败");
    }
  }, [projectId, docId, selectedSectionId, newComment]);

  const filteredSections = useMemo(() => {
    if (statusFilter === "all") return sections;
    return sections.filter((s) => s.status === statusFilter);
  }, [sections, statusFilter]);

  const isLockedByOther =
    selectedSection?.locked_by != null &&
    selectedSection.locked_by_name != null;

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Section sidebar */}
      {sidebarOpen && (
        <div className="w-56 shrink-0 border-r flex flex-col">
          <div className="p-2 border-b flex items-center justify-between">
            <span className="text-sm font-medium">章节列表</span>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setSidebarOpen(false)}
            >
              <PanelLeftCloseIcon className="size-3" />
            </Button>
          </div>
          <div className="p-2 border-b">
            <select
              className="w-full rounded border px-2 py-1 text-xs bg-transparent"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="all">全部状态</option>
              <option value="raw">原始</option>
              <option value="cleaning">清洗中</option>
              <option value="cleaned">已清洗</option>
              <option value="in_review">审核中</option>
              <option value="approved">已通过</option>
              <option value="rejected">已拒绝</option>
            </select>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-1">
              {filteredSections.map((section) => (
                <button
                  key={section.id}
                  className={`w-full text-left rounded px-2 py-1.5 text-xs transition-colors ${
                    selectedSectionId === section.id
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-muted"
                  }`}
                  onClick={() => setSelectedSectionId(section.id)}
                >
                  <div className="flex items-center gap-1 justify-between">
                    <span className="truncate flex-1">
                      {section.section_index + 1}. {section.title || "无标题"}
                    </span>
                    <StatusBadge
                      status={section.status}
                      className="text-[9px] px-1 py-0"
                    />
                  </div>
                  {section.locked_by_name && (
                    <div className="flex items-center gap-0.5 text-[10px] text-orange-600 mt-0.5">
                      <LockIcon className="size-2.5" />
                      {section.locked_by_name}
                    </div>
                  )}
                </button>
              ))}
              {filteredSections.length === 0 && (
                <div className="px-2 py-4 text-center text-xs text-muted-foreground">
                  无匹配章节
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      )}

      {/* Main 4-column workspace */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3 py-2 border-b">
          {!sidebarOpen && (
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setSidebarOpen(true)}
            >
              <PanelLeftOpenIcon className="size-3" />
            </Button>
          )}
          {isLockedByOther && (
            <Badge variant="secondary" className="bg-orange-100 text-orange-700 text-xs">
              <LockIcon className="size-3 mr-1" />
              已被 {selectedSection?.locked_by_name} 锁定
            </Badge>
          )}
          <div className="flex-1" />
          <Button
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={saving || isLockedByOther}
          >
            <SaveIcon className="size-3" />
            保存
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleSubmitReview}
          >
            <SendIcon className="size-3" />
            提交审核
          </Button>
          <Button variant="outline" size="sm" onClick={handleApprove}>
            <CheckIcon className="size-3" />
            通过
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleReject}
          >
            <XIcon className="size-3" />
            驳回
          </Button>
        </div>

        {/* 4-column grid */}
        <div className="flex-1 grid grid-cols-4 min-h-0">
          {/* Column 1: PDF viewer */}
          <div className="border-r flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              PDF 原文
            </div>
            <div className="flex-1 min-h-0">
              {pdfUrl ? (
                <iframe
                  src={pdfUrl}
                  className="w-full h-full border-0"
                  title="PDF预览"
                />
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  无PDF文件
                </div>
              )}
            </div>
          </div>

          {/* Column 2: Raw markdown (read-only) */}
          <div className="border-r flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              原始Markdown
            </div>
            <ScrollArea className="flex-1">
              <pre className="p-3 text-xs whitespace-pre-wrap font-mono leading-relaxed">
                {selectedSection?.raw_markdown || "暂无内容"}
              </pre>
            </ScrollArea>
          </div>

          {/* Column 3: CodeMirror editor */}
          <div className="border-r flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              清洗编辑器
            </div>
            <div className="flex-1 min-h-0 overflow-auto">
              <CodeMirrorEditor
                value={editedMarkdown}
                onChange={setEditedMarkdown}
                readOnly={isLockedByOther}
              />
            </div>
          </div>

          {/* Column 4: Preview + Comments */}
          <div className="flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              预览 & 评论
            </div>
            <div className="flex-1 flex flex-col min-h-0">
              {/* Markdown preview */}
              <ScrollArea className="flex-1 border-b">
                <div className="p-3 prose prose-sm max-w-none dark:prose-invert">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {editedMarkdown || "暂无内容"}
                  </ReactMarkdown>
                </div>
              </ScrollArea>
              {/* Comments */}
              <div className="h-48 shrink-0 flex flex-col">
                <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50 flex items-center gap-1">
                  <MessageSquareIcon className="size-3" />
                  评论
                </div>
                <ScrollArea className="flex-1">
                  <div className="p-2 space-y-2">
                    {comments.map((c) => (
                      <div key={c.id} className="text-xs">
                        <div className="flex items-center gap-1">
                          <span className="font-medium">
                            {c.author_name || "用户"}
                          </span>
                          <span className="text-muted-foreground">
                            {new Date(c.created_at).toLocaleString("zh-CN")}
                          </span>
                        </div>
                        <div className="mt-0.5">{c.content}</div>
                      </div>
                    ))}
                    {comments.length === 0 && (
                      <div className="text-xs text-muted-foreground text-center py-2">
                        暂无评论
                      </div>
                    )}
                  </div>
                </ScrollArea>
                <div className="p-2 border-t flex gap-1">
                  <Textarea
                    value={newComment}
                    onChange={(e) => setNewComment(e.target.value)}
                    placeholder="输入评论..."
                    className="min-h-6 h-6 text-xs"
                  />
                  <Button size="xs" onClick={handleAddComment}>
                    发送
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
