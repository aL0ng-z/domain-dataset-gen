"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
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
  ArrowLeftIcon,
  LockIcon,
  SaveIcon,
  SendIcon,
  CheckIcon,
  XIcon,
  MessageSquareIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

// Dynamically import CodeMirror to avoid SSR issues
const CodeMirrorEditor = dynamic(
  () => import("./codemirror-editor").then((m) => ({ default: m.CodeMirrorEditor })),
  { ssr: false, loading: () => <div className="p-4 text-sm text-muted-foreground">编辑器加载中...</div> }
);

interface Section {
  id: string;
  ordinal: number;
  heading_path: string;
  status: string;
  locked_by?: string;
  locked_by_name?: string;
  raw_markdown?: string;
  cleaned_markdown?: string;
  assignment_status: "unassigned" | "assigned" | "in_progress" | "completed" | "returned";
  assigned_to: string | null;
  return_reason?: string | null;
}

interface Comment {
  id: string;
  content: string;
  comment_type: string;
  user_id: string;
  created_at: string;
}

interface CleanedVersion {
  id: string;
  document_id: string;
  version: number;
  section_count: number;
  status: "draft" | "review_pending" | "accepted" | "rejected";
  created_by: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
}

interface ProjectMember {
  user_id: string;
  role: string;
  username?: string;  // server sometimes returns this; guard for undefined
}

interface CurrentUser {
  id: string;
  username: string;
  role: "admin" | "reviewer" | "editor" | "viewer";
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
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [versions, setVersions] = useState<CleanedVersion[]>([]);
  const [assignFilter, setAssignFilter] = useState<"all" | "mine" | "mine_pending" | "unassigned">("all");
  const [selectedForAssign, setSelectedForAssign] = useState<Set<string>>(new Set());
  const [assigneePick, setAssigneePick] = useState<string>("");

  // PDF URL: direct backend URL with auth token
  const pdfUrl = useMemo(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    if (!token) return "";
    return `${API_BASE}/projects/${projectId}/documents/${docId}/file?token=${encodeURIComponent(token)}`;
  }, [projectId, docId]);

  // Fetch sections list (no selectedSectionId dep to avoid refetch loop)
  const fetchSections = useCallback(() => {
    api
      .get<{ items: Section[] }>(
        `/projects/${projectId}/documents/${docId}/sections?page=1&page_size=100`
      )
      .then((data) => {
        setSections(data.items);
      })
      .catch(() => toast.error("加载章节列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId]);

  useEffect(() => {
    fetchSections();
  }, [fetchSections]);

  useEffect(() => {
    api.get<CurrentUser>("/auth/me")
      .then((u) => {
        setCurrentUser(u);
        if (u.role === "editor") setAssignFilter("mine");
      })
      .catch(() => {});
    api.get<ProjectMember[]>(`/projects/${projectId}/members`)
      .then((m) => setMembers(m))
      .catch(() => setMembers([]));
  }, [projectId]);

  const fetchVersions = useCallback(() => {
    api.get<CleanedVersion[]>(`/projects/${projectId}/documents/${docId}/cleaning/versions`)
      .then((v) => setVersions(v))
      .catch(() => setVersions([]));
  }, [projectId, docId]);

  useEffect(() => { fetchVersions(); }, [fetchVersions]);

  // Auto-select first section when list loads and nothing is selected
  useEffect(() => {
    if (!selectedSectionId && sections.length > 0) {
      setSelectedSectionId(sections[0].id);
    }
  }, [sections, selectedSectionId]);

  // Fetch selected section detail + comments
  useEffect(() => {
    if (!selectedSectionId) return;
    api
      .get<Section>(`/sections/${selectedSectionId}`)
      .then((section) => {
        setSelectedSection(section);
        setEditedMarkdown(section.cleaned_markdown || section.raw_markdown || "");
      })
      .catch(() => toast.error("加载章节详情失败"));

    // Comments API returns list (not paginated)
    api
      .get<Comment[]>(`/sections/${selectedSectionId}/comments`)
      .then((data) => setComments(data))
      .catch(() => setComments([]));
  }, [selectedSectionId]);

  // Acquire lease on selection, then keep it alive; release on switch/unmount.
  useEffect(() => {
    if (!selectedSectionId) return;

    let isActive = true;
    let leaseAcquired = false;
    let interval: ReturnType<typeof setInterval> | null = null;

    const stopHeartbeat = () => {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
    };

    const releaseLease = async () => {
      if (!leaseAcquired) return;
      leaseAcquired = false;
      try {
        await api.post(`/sections/${selectedSectionId}/lease/release`);
      } catch {
        // Ignore release failures during navigation/unmount.
      }
    };

    const acquireLease = async () => {
      try {
        await api.post(`/sections/${selectedSectionId}/lease/acquire`);
        leaseAcquired = true;

        if (!isActive) {
          await releaseLease();
          return;
        }

        interval = setInterval(() => {
          api.post(`/sections/${selectedSectionId}/lease/heartbeat`).catch(() => {
            stopHeartbeat();
            leaseAcquired = false;
          });
        }, 30000);
      } catch {
        // If acquire fails, do not start heartbeat polling.
      }
    };

    void acquireLease();

    return () => {
      isActive = false;
      stopHeartbeat();
      void releaseLease();
    };
  }, [selectedSectionId]);

  const handleSave = useCallback(async () => {
    if (!selectedSectionId) return;
    setSaving(true);
    try {
      await api.patch(`/sections/${selectedSectionId}`, { cleaned_markdown: editedMarkdown });
      toast.success("保存成功");
      fetchSections();
    } catch {
      toast.error("保存失败");
    } finally {
      setSaving(false);
    }
  }, [selectedSectionId, editedMarkdown, fetchSections]);

  const handleSubmitReview = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(`/sections/${selectedSectionId}/submit`);
      toast.success("已提交审核");
      fetchSections();
    } catch {
      toast.error("提交审核失败");
    }
  }, [selectedSectionId, fetchSections]);

  const handleApprove = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(`/sections/${selectedSectionId}/review`, { action: "accept" });
      toast.success("已通过");
      fetchSections();
    } catch {
      toast.error("操作失败");
    }
  }, [selectedSectionId, fetchSections]);

  const handleReject = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(`/sections/${selectedSectionId}/review`, { action: "reject" });
      toast.success("已驳回");
      fetchSections();
    } catch {
      toast.error("操作失败");
    }
  }, [selectedSectionId, fetchSections]);

  const handleAddComment = useCallback(async () => {
    if (!selectedSectionId || !newComment.trim()) return;
    try {
      await api.post(`/sections/${selectedSectionId}/comments`, { content: newComment });
      setNewComment("");
      const data = await api.get<Comment[]>(`/sections/${selectedSectionId}/comments`);
      setComments(data);
    } catch {
      toast.error("评论失败");
    }
  }, [selectedSectionId, newComment]);

  const toggleAssignSelect = (sid: string) => {
    setSelectedForAssign((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid); else next.add(sid);
      return next;
    });
  };

  const handleBulkAssign = useCallback(async () => {
    if (!assigneePick || selectedForAssign.size === 0) {
      toast.error("请选择章节和指派对象");
      return;
    }
    try {
      await api.post(`/projects/${projectId}/documents/${docId}/cleaning/assign`, {
        assignments: [{ section_ids: Array.from(selectedForAssign), assignee_id: assigneePick }],
      });
      toast.success("已分派");
      setSelectedForAssign(new Set());
      fetchSections();
    } catch {
      toast.error("分派失败");
    }
  }, [assigneePick, selectedForAssign, projectId, docId, fetchSections]);

  const handleComplete = useCallback(async () => {
    if (!selectedSectionId) return;
    try {
      await api.post(`/sections/${selectedSectionId}/complete`);
      toast.success("已标记完成");
      fetchSections();
    } catch {
      toast.error("标记完成失败");
    }
  }, [selectedSectionId, fetchSections]);

  const handleReturn = useCallback(async () => {
    if (!selectedSectionId) return;
    const reason = window.prompt("请输入退回原因：");
    if (!reason) return;
    try {
      await api.post(`/sections/${selectedSectionId}/return`, { reason });
      toast.success("已退回");
      fetchSections();
    } catch {
      toast.error("退回失败");
    }
  }, [selectedSectionId, fetchSections]);

  const handleMerge = useCallback(async () => {
    try {
      await api.post(`/projects/${projectId}/documents/${docId}/cleaning/merge`);
      toast.success("已生成合并版本");
      fetchVersions();
    } catch {
      toast.error("合并失败");
    }
  }, [projectId, docId, fetchVersions]);

  const isAdmin = currentUser?.role === "admin" || currentUser?.role === "reviewer";

  const filteredSections = useMemo(() => {
    let base = sections;
    if (assignFilter === "mine" && currentUser) {
      base = base.filter((s) => s.assigned_to === currentUser.id);
    } else if (assignFilter === "mine_pending" && currentUser) {
      base = base.filter(
        (s) => s.assigned_to === currentUser.id && s.assignment_status !== "completed"
      );
    } else if (assignFilter === "unassigned") {
      base = base.filter((s) => s.assignment_status === "unassigned");
    }
    if (statusFilter !== "all") base = base.filter((s) => s.status === statusFilter);
    return base;
  }, [sections, assignFilter, statusFilter, currentUser]);

  const completionStats = useMemo(() => {
    const total = sections.length;
    const completed = sections.filter((s) => s.assignment_status === "completed").length;
    return { total, completed };
  }, [sections]);

  const latestVersion = versions[0];

  const handleFinalReview = useCallback(async (action: "accept" | "reject") => {
    if (!latestVersion) return;
    const reason = action === "reject" ? window.prompt("驳回原因：") ?? undefined : undefined;
    try {
      await api.post(`/projects/${projectId}/documents/${docId}/cleaning/final-review`, {
        version_id: latestVersion.id, action, reason,
      });
      toast.success(action === "accept" ? "已通过" : "已驳回");
      fetchVersions();
    } catch {
      toast.error("操作失败");
    }
  }, [latestVersion, projectId, docId, fetchVersions]);

  const isLockedByOther =
    selectedSection?.locked_by != null &&
    selectedSection.locked_by_name != null;

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">加载中...</div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Section sidebar */}
      {sidebarOpen && (
        <div className="w-56 shrink-0 border-r flex flex-col">
          <div className="p-2 border-b flex items-center justify-between">
            <Link
              href={`/projects/${projectId}/documents/${docId}`}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeftIcon className="size-3" />
              返回文档
            </Link>
            <Button variant="ghost" size="icon-xs" onClick={() => setSidebarOpen(false)}>
              <PanelLeftCloseIcon className="size-3" />
            </Button>
          </div>
          <div className="p-2 border-b space-y-2">
            <div className="flex flex-wrap gap-1">
              {(["all", "mine", "mine_pending", "unassigned"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setAssignFilter(v)}
                  className={`text-[10px] px-1.5 py-0.5 rounded border ${
                    assignFilter === v ? "bg-accent text-accent-foreground" : "bg-transparent"
                  }`}
                >
                  {v === "all" ? "全部" : v === "mine" ? "分派给我" : v === "mine_pending" ? "我未完成" : "未分派"}
                </button>
              ))}
            </div>
            {isAdmin && (
              <div className="space-y-1">
                <select
                  className="w-full rounded border px-2 py-1 text-xs bg-transparent"
                  value={assigneePick}
                  onChange={(e) => setAssigneePick(e.target.value)}
                >
                  <option value="">选择指派对象...</option>
                  {members.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.username || m.user_id.slice(0, 8)} ({m.role})
                    </option>
                  ))}
                </select>
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full text-xs"
                  disabled={selectedForAssign.size === 0 || !assigneePick}
                  onClick={handleBulkAssign}
                >
                  分派 {selectedForAssign.size} 个章节
                </Button>
              </div>
            )}
          </div>
          <div className="p-2 border-b">
            <select
              className="w-full rounded border px-2 py-1 text-xs bg-transparent"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="all">全部状态</option>
              <option value="draft">草稿</option>
              <option value="in_cleaning">清洗中</option>
              <option value="review_pending">待审核</option>
              <option value="accepted">已通过</option>
              <option value="rejected">已驳回</option>
            </select>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-1">
              {filteredSections.map((section) => {
                const assignee = members.find((m) => m.user_id === section.assigned_to);
                return (
                  <div key={section.id} className="flex items-center gap-1 px-1">
                    {isAdmin && (
                      <input
                        type="checkbox"
                        checked={selectedForAssign.has(section.id)}
                        onChange={() => toggleAssignSelect(section.id)}
                        className="shrink-0"
                      />
                    )}
                    <button
                      className={`flex-1 text-left rounded px-2 py-1.5 text-xs transition-colors ${
                        selectedSectionId === section.id ? "bg-accent text-accent-foreground" : "hover:bg-muted"
                      }`}
                      onClick={() => setSelectedSectionId(section.id)}
                    >
                      <div className="flex items-center gap-1 justify-between">
                        <span className="truncate flex-1">{section.ordinal + 1}. {section.heading_path || "无标题"}</span>
                        <StatusBadge status={section.status} className="text-[9px] px-1 py-0" />
                      </div>
                      <div className="flex items-center gap-2 text-[10px] mt-0.5">
                        <span className={`px-1 rounded ${
                          section.assignment_status === "completed" ? "bg-green-100 text-green-700" :
                          section.assignment_status === "in_progress" ? "bg-blue-100 text-blue-700" :
                          section.assignment_status === "assigned" ? "bg-yellow-100 text-yellow-700" :
                          section.assignment_status === "returned" ? "bg-red-100 text-red-700" :
                          "bg-gray-100 text-gray-600"
                        }`}>
                          {section.assignment_status}
                        </span>
                        {assignee && <span className="text-muted-foreground">{assignee.username || assignee.user_id.slice(0, 8)}</span>}
                      </div>
                      {section.locked_by_name && (
                        <div className="flex items-center gap-0.5 text-[10px] text-orange-600 mt-0.5">
                          <LockIcon className="size-2.5" />
                          {section.locked_by_name}
                        </div>
                      )}
                    </button>
                  </div>
                );
              })}
              {filteredSections.length === 0 && (
                <div className="px-2 py-4 text-center text-xs text-muted-foreground">无匹配章节</div>
              )}
            </div>
          </ScrollArea>
        </div>
      )}

      {/* Main 3-column workspace */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3 py-2 border-b">
          {!sidebarOpen && (
            <Button variant="ghost" size="icon-xs" onClick={() => setSidebarOpen(true)}>
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
          <Button variant="outline" size="sm" onClick={handleSave} disabled={saving || isLockedByOther}>
            <SaveIcon className="size-3" />
            保存
          </Button>
          <Button variant="outline" size="sm" onClick={handleSubmitReview}>
            <SendIcon className="size-3" />
            提交审核
          </Button>
          {selectedSection?.assigned_to === currentUser?.id && selectedSection?.assignment_status !== "completed" && (
            <Button variant="outline" size="sm" onClick={handleComplete}>
              <CheckIcon className="size-3" />
              完成 (分派)
            </Button>
          )}
          {isAdmin && selectedSection?.assignment_status === "completed" && (
            <Button variant="outline" size="sm" onClick={handleReturn}>
              <XIcon className="size-3" />
              退回
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={handleApprove}>
            <CheckIcon className="size-3" />
            通过
          </Button>
          <Button variant="outline" size="sm" onClick={handleReject}>
            <XIcon className="size-3" />
            驳回
          </Button>
        </div>

        {/* Completion bar + merged-version controls */}
        <div className="flex items-center gap-2 px-3 py-1.5 border-b bg-muted/30 text-xs">
          <span>完成进度：{completionStats.completed} / {completionStats.total}</span>
          {isAdmin && (
            <Button
              size="xs"
              variant="outline"
              onClick={handleMerge}
              disabled={completionStats.completed === 0}
            >
              生成合并版本
            </Button>
          )}
          {latestVersion && (
            <>
              <Badge variant="secondary">
                v{latestVersion.version} · {latestVersion.status}
              </Badge>
              {isAdmin && latestVersion.status === "review_pending" && (
                <>
                  <Button size="xs" variant="outline" onClick={() => handleFinalReview("accept")}>
                    <CheckIcon className="size-3" /> 通过
                  </Button>
                  <Button size="xs" variant="outline" onClick={() => handleFinalReview("reject")}>
                    <XIcon className="size-3" /> 驳回
                  </Button>
                  <Link
                    href={`#`}
                    onClick={async (e) => {
                      e.preventDefault();
                      const ver = await api.get<CleanedVersion & { merged_markdown: string }>(`/cleaned-versions/${latestVersion.id}`);
                      const w = window.open("", "_blank");
                      if (w) {
                        w.document.write(`<pre style="white-space:pre-wrap;padding:16px;font-family:ui-monospace,monospace">${ver.merged_markdown.replace(/</g, "&lt;")}</pre>`);
                        w.document.title = `合并版本 v${latestVersion.version}`;
                      }
                    }}
                    className="text-xs text-blue-600 underline"
                  >
                    查看全文
                  </Link>
                </>
              )}
            </>
          )}
        </div>

        {/* 3-column grid: PDF | Preview | Editor + Comments */}
        <div className="flex-1 grid grid-cols-3 min-h-0">
          {/* Column 1: PDF viewer */}
          <div className="border-r flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              PDF 原文
            </div>
            <div className="flex-1 min-h-0">
              {pdfUrl ? (
                <iframe src={pdfUrl} className="w-full h-full border-0" title="PDF预览" />
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  无PDF文件
                </div>
              )}
            </div>
          </div>

          {/* Column 2: Live Markdown preview */}
          <div className="border-r flex flex-col min-h-0 overflow-hidden">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              Markdown 预览
            </div>
            <ScrollArea className="flex-1 min-h-0">
              <div className="p-3 prose prose-sm max-w-none dark:prose-invert [&_ol]:list-decimal">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    ol: ({ node, start, ...props }) => {
                      const s = typeof start === "number" ? start : undefined;
                      return <ol start={s} style={s && s > 1 ? { counterReset: `list-item ${s - 1}` } : undefined} {...props} />;
                    },
                  }}
                >
                  {editedMarkdown || "暂无内容"}
                </ReactMarkdown>
              </div>
            </ScrollArea>
          </div>

          {/* Column 3: Editor + Comments */}
          <div className="flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              Markdown 编辑器
            </div>
            <div className="flex-1 min-h-0 overflow-auto">
              <CodeMirrorEditor
                value={editedMarkdown}
                onChange={setEditedMarkdown}
                readOnly={isLockedByOther}
              />
            </div>
            {/* Comments panel */}
            <div className="h-48 shrink-0 border-t flex flex-col">
              <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50 flex items-center gap-1">
                <MessageSquareIcon className="size-3" />
                评论
              </div>
              <ScrollArea className="flex-1">
                <div className="p-2 space-y-2">
                  {comments.map((c) => (
                    <div key={c.id} className="text-xs">
                      <div className="flex items-center gap-1">
                        <span className="font-medium">{c.user_id?.slice(0, 8) || "用户"}</span>
                        <span className="text-muted-foreground">
                          {new Date(c.created_at).toLocaleString("zh-CN")}
                        </span>
                      </div>
                      <div className="mt-0.5">{c.content}</div>
                    </div>
                  ))}
                  {comments.length === 0 && (
                    <div className="text-xs text-muted-foreground text-center py-2">暂无评论</div>
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
  );
}
