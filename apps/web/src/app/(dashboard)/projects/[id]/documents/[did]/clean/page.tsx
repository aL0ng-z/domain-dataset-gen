"use client";

import { useProjectAccess } from "@/hooks/use-project-access";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StatusBadge } from "@/components/status-badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { useWs } from "@/hooks/use-ws";
import { useCleaningWorkbench, isBusinessError } from "@/hooks/use-cleaning-workbench";
import {
  ArrowLeftIcon,
  SaveIcon,
  SendIcon,
  CheckIcon,
  XIcon,
  MessageSquareIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  ClipboardIcon,
  RefreshCcwIcon,
} from "lucide-react";

// Dynamically import CodeMirror to avoid SSR issues
const CodeMirrorEditor = dynamic(
  () => import("./codemirror-editor").then((m) => ({ default: m.CodeMirrorEditor })),
  { ssr: false, loading: () => <div className="p-4 text-sm text-muted-foreground">编辑器加载中...</div> }
);

type Section = components["schemas"]["SectionResponse"];
type CleanedVersion = components["schemas"]["CleanedDocumentVersionResponse"];
type CleaningJobContext = components["schemas"]["CleaningJobResponse"];
type ProjectMember = components["schemas"]["ProjectMemberResponse"];
type CurrentUser = components["schemas"]["UserResponse"];

type MarkdownAstNode = {
  type?: string;
  value?: string;
  children?: MarkdownAstNode[];
};

function remarkSoftLineBreaks() {
  return (tree: MarkdownAstNode) => {
    preserveTextLineBreaks(tree);
  };
}

function preserveTextLineBreaks(node: MarkdownAstNode) {
  if (!node.children) return;

  const nextChildren: MarkdownAstNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string" && child.value.includes("\n")) {
      const parts = child.value.split("\n");
      parts.forEach((part, index) => {
        if (index > 0) nextChildren.push({ type: "break" });
        if (part) nextChildren.push({ ...child, value: part });
      });
      continue;
    }

    preserveTextLineBreaks(child);
    nextChildren.push(child);
  }

  node.children = nextChildren;
}

function looksLikeMathFormula(value: string) {
  return /[=^_{}]|\\[a-zA-Z]+/.test(value);
}

function normalizeMarkdownForPreview(markdown: string) {
  return markdown
    .replace(/\r\n?/g, "\n")
    .replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, formula: string) => (
      looksLikeMathFormula(formula) ? `\n\n$$\n${formula.trim()}\n$$\n\n` : `[${formula}]`
    ))
    .replace(/\\\((.+?)\\\)/g, (_match, formula: string) => (
      looksLikeMathFormula(formula) ? `$${formula}$` : `(${formula})`
    ))
    .replace(/\\([\\`*{}\[\]()#+\-.!_$>|~=])/g, "$1");
}

/** 一次“合并清洗版本”用户意图对应一个幂等键；未知结果时按 key 查询而非换 key 重试。 */
function makeMergeIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `merge-${crypto.randomUUID()}`;
  }
  return `merge-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function CleaningWorkbenchPage() {
  const params = useParams<{ id: string; did: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = params.id;
  const docId = params.did;
  const cleaningJobId = searchParams.get("cleaning_job_id");

  const [sections, setSections] = useState<Section[]>([]);
  const [cleaningJobs, setCleaningJobs] = useState<CleaningJobContext[]>([]);
  const [newComment, setNewComment] = useState("");
  const [loading, setLoading] = useState(true);
  const [contextLoading, setContextLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [versions, setVersions] = useState<CleanedVersion[]>([]);
  const [assignFilter, setAssignFilter] = useState<"all" | "mine" | "mine_pending" | "unassigned">("all");
  const [selectedForAssign, setSelectedForAssign] = useState<Set<string>>(new Set());
  const [assigneePick, setAssigneePick] = useState<string>("");
  const { lastMessage } = useWs();
  const displayedContextId = useRef<string | null>(cleaningJobId);
  displayedContextId.current = cleaningJobId;

  // dirty guard：导航/切换前确认（保存 / 放弃 / 留在当前页）。
  const [guardPrompt, setGuardPrompt] = useState<{
    pendingTarget: () => void;
  } | null>(null);
  const [merging, setMerging] = useState(false);

  const workbench = useCleaningWorkbench();
  const { setSelectedSectionId } = workbench;

  const cleaningUrlFor = useCallback((id: string) => (
    `/projects/${projectId}/documents/${docId}/clean?cleaning_job_id=${encodeURIComponent(id)}`
  ), [projectId, docId]);

  const switchCleaningContext = useCallback((id: string) => {
    displayedContextId.current = id;
    setLoading(true);
    setSections([]);
    workbench.resetForCleanup();
    setVersions([]);
    setSelectedForAssign(new Set());
    router.push(cleaningUrlFor(id));
  }, [router, cleaningUrlFor, workbench]);

  const activeCleaningJob = cleaningJobs.find((job) => job.id === cleaningJobId);

  const fetchCleaningJobs = useCallback(() => {
    api.get("/projects/{pid}/documents/{did}/cleaning-jobs", {
      params: { pid: projectId, did: docId },
    })
      .then((jobs) => {
        setCleaningJobs(jobs);
        if (!cleaningJobId && jobs.length > 0) {
          router.replace(cleaningUrlFor(jobs[0].id));
        } else if (cleaningJobId && jobs.length > 0 && !jobs.some((job) => job.id === cleaningJobId)) {
          toast.error("指定的清洗来源不存在，已切换到最近一次清洗任务");
          router.replace(cleaningUrlFor(jobs[0].id));
        } else if (jobs.length === 0) {
          setLoading(false);
        }
      })
      .catch(() => {
        toast.error("加载清洗来源失败");
        setLoading(false);
      })
      .finally(() => setContextLoading(false));
  }, [projectId, docId, cleaningJobId, router, cleaningUrlFor]);

  useEffect(() => {
    fetchCleaningJobs();
  }, [fetchCleaningJobs]);

  // PDF Blob 下载：经 apiFetch 携带 Authorization（任务卡 §5.2、§6）。
  const [pdfUrl, setPdfUrl] = useState("");
  const [pdfError, setPdfError] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setPdfError(false);
    setPdfUrl("");

    api
      .getBlob(`/projects/${projectId}/documents/${docId}/file`)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPdfUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setPdfError(true);
      });

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    };
  }, [projectId, docId]);

  // Fetch sections list
  const fetchSections = useCallback(() => {
    if (!cleaningJobId) return;
    api
      .get("/projects/{pid}/documents/{did}/sections", {
        params: { pid: projectId, did: docId },
        query: { page: 1, page_size: 100, cleaning_job_id: cleaningJobId },
      })
      .then((data) => {
        if (displayedContextId.current !== cleaningJobId) return;
        setSections(data.items);
        setSelectedSectionId((current) => (
          data.items.some((section) => section.id === current)
            ? current
            : data.items[0]?.id ?? null
        ));
        setSelectedForAssign(new Set());
      })
      .catch(() => {
        if (displayedContextId.current === cleaningJobId) toast.error("加载章节列表失败");
      })
      .finally(() => {
        if (displayedContextId.current === cleaningJobId) setLoading(false);
      });
  }, [projectId, docId, cleaningJobId, setSelectedSectionId]);

  useEffect(() => {
    fetchSections();
  }, [fetchSections]);

  useEffect(() => {
    api.get("/auth/me")
      .then((u) => {
        setCurrentUser(u);
        if (u.role === "editor") setAssignFilter("mine");
      })
      .catch(() => {});
    api.get("/projects/{pid}/members", { params: { pid: projectId } })
      .then((m) => setMembers(m))
      .catch(() => setMembers([]));
  }, [projectId]);

  const fetchVersions = useCallback(() => {
    if (!cleaningJobId) return;
    api.get("/projects/{pid}/documents/{did}/cleaning/versions", {
      params: { pid: projectId, did: docId },
      query: { cleaning_job_id: cleaningJobId },
    })
      .then((v) => {
        if (displayedContextId.current === cleaningJobId) setVersions(v);
      })
      .catch(() => {
        if (displayedContextId.current === cleaningJobId) setVersions([]);
      });
  }, [projectId, docId, cleaningJobId]);

  useEffect(() => { fetchVersions(); }, [fetchVersions]);

  useEffect(() => {
    if (!lastMessage || !cleaningJobId) return;
    fetchCleaningJobs();
    fetchSections();
    fetchVersions();
  }, [lastMessage, cleaningJobId, fetchCleaningJobs, fetchSections, fetchVersions]);

  useEffect(() => {
    if (!activeCleaningJob || !["queued", "processing"].includes(activeCleaningJob.status)) return;
    const refreshTimer = window.setInterval(() => {
      fetchCleaningJobs();
      fetchSections();
    }, 1500);
    return () => window.clearInterval(refreshTimer);
  }, [activeCleaningJob, fetchCleaningJobs, fetchSections]);

  // 首次选中 section 后加载详情 + 租约；cleanup 由 hook 内的 effect 负责（只释放自身 lease）。
  useEffect(() => {
    if (!workbench.selectedSectionId) return;
    if (workbench.acquiringLease || workbench.selectedSection?.id === workbench.selectedSectionId) return;
    workbench.loadSection(workbench.selectedSectionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workbench.selectedSectionId]);

  // 保存 / 提交：工具栏按钮触发。
  const handleSave = useCallback(async () => {
    const saved = await workbench.save();
    if (saved.ok) fetchSections();
  }, [workbench, fetchSections]);

  const handleSubmitReview = useCallback(async () => {
    const ok = await workbench.submit();
    if (ok) {
      toast.success("已提交审核");
      fetchSections();
    }
  }, [workbench, fetchSections]);

  const handleApprove = useCallback(async () => {
    if (!workbench.selectedSectionId) return;
    try {
      await api.post("/sections/{sid}/review", { action: "accept" }, { params: { sid: workbench.selectedSectionId } });
      toast.success("已通过");
      fetchSections();
    } catch {
      toast.error("操作失败");
    }
  }, [workbench.selectedSectionId, fetchSections]);

  const handleReject = useCallback(async () => {
    if (!workbench.selectedSectionId) return;
    try {
      await api.post("/sections/{sid}/review", { action: "reject" }, { params: { sid: workbench.selectedSectionId } });
      toast.success("已驳回");
      fetchSections();
    } catch {
      toast.error("操作失败");
    }
  }, [workbench.selectedSectionId, fetchSections]);

  const handleAddComment = useCallback(async () => {
    if (!workbench.selectedSectionId || !newComment.trim()) return;
    try {
      await api.post("/sections/{sid}/comments", { comment_type: "general", content: newComment }, { params: { sid: workbench.selectedSectionId } });
      setNewComment("");
      const data = await api.get("/sections/{sid}/comments", { params: { sid: workbench.selectedSectionId } });
      workbench.setComments(data);
    } catch {
      toast.error("评论失败");
    }
  }, [workbench, newComment]);

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
      if (!cleaningJobId) return;
      await api.post("/projects/{pid}/documents/{did}/cleaning/assign", {
        assignments: [{ section_ids: Array.from(selectedForAssign), assignee_id: assigneePick }],
      }, {
        params: { pid: projectId, did: docId },
        query: { cleaning_job_id: cleaningJobId },
      });
      toast.success("已分派");
      setSelectedForAssign(new Set());
      fetchSections();
    } catch {
      toast.error("分派失败");
    }
  }, [assigneePick, selectedForAssign, projectId, docId, cleaningJobId, fetchSections]);

  const handleComplete = useCallback(async () => {
    if (!workbench.selectedSectionId) return;
    try {
      await api.post("/sections/{sid}/complete", undefined, { params: { sid: workbench.selectedSectionId } });
      toast.success("已标记完成");
      fetchSections();
    } catch {
      toast.error("标记完成失败");
    }
  }, [workbench.selectedSectionId, fetchSections]);

  const handleReturn = useCallback(async () => {
    if (!workbench.selectedSectionId) return;
    const reason = window.prompt("请输入退回原因：");
    if (!reason) return;
    try {
      await api.post("/sections/{sid}/return", { reason }, { params: { sid: workbench.selectedSectionId } });
      toast.success("已退回");
      fetchSections();
    } catch {
      toast.error("退回失败");
    }
  }, [workbench.selectedSectionId, fetchSections]);

  // 合并：一次用户意图生成并复用同一个幂等键；未知结果按 key 查询。
  const mergeIdempotencyRef = useRef<string | null>(null);

  const doMerge = useCallback(async () => {
    if (!cleaningJobId) return;
    if (!mergeIdempotencyRef.current) {
      mergeIdempotencyRef.current = makeMergeIdempotencyKey();
    }
    const idemKey = mergeIdempotencyRef.current;
    setMerging(true);
    try {
      const version = await api.post("/projects/{pid}/documents/{did}/cleaning/merge", undefined, {
        params: { pid: projectId, did: docId },
        query: { cleaning_job_id: cleaningJobId },
        headers: { "Idempotency-Key": idemKey },
      });
      toast.success(`已生成合并版本 v${version.version}`);
      mergeIdempotencyRef.current = null; // 成功后允许下一次新意图
      fetchVersions();
    } catch (e) {
      if (isBusinessError(e, "CLEAN_SOURCE_CHANGED")) {
        toast.error("来源章节已变化，请确认后重新合并");
        mergeIdempotencyRef.current = null; // 来源变化：新合并意图，刷新提示后用户确认
      } else {
        toast.error("合并失败");
      }
      fetchVersions();
    } finally {
      setMerging(false);
    }
  }, [projectId, docId, cleaningJobId, fetchVersions]);

  const handleMerge = useCallback(() => {
    if (!workbench.isDirty) {
      void doMerge();
    } else {
      setGuardPrompt({
        pendingTarget: () => void doMerge(),
      });
    }
  }, [workbench.isDirty, doMerge]);

  const { canReview: isAdmin } = useProjectAccess(projectId);

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
  const previewMarkdown = useMemo(
    () => normalizeMarkdownForPreview(workbench.editedMarkdown),
    [workbench.editedMarkdown]
  );

  // final-review：stale/conflict 时刷新版本列表与 Document active pointer，不本地覆盖赢家。
  const handleFinalReview = useCallback(async (action: "accept" | "reject") => {
    if (!latestVersion) return;
    if (!cleaningJobId) return;
    const reason = action === "reject" ? window.prompt("驳回原因：") ?? undefined : undefined;
    try {
      await api.post("/projects/{pid}/documents/{did}/cleaning/final-review", {
        version_id: latestVersion.id, action, reason,
      }, {
        params: { pid: projectId, did: docId },
        query: { cleaning_job_id: cleaningJobId },
      });
      toast.success(action === "accept" ? "已通过" : "已驳回");
      fetchVersions();
      fetchSections();
    } catch (e) {
      if (isBusinessError(e, "CLEAN_VERSION_STALE") || isBusinessError(e, "CLEAN_VERSION_REVIEW_CONFLICT")) {
        toast.error(action === "accept" ? "该版本已过期或已被处理，请刷新查看最新状态" : "该版本已被处理");
        // 不本地覆盖赢家状态：刷新版本列表与 Document active pointer。
        fetchVersions();
        fetchSections();
      } else {
        toast.error("操作失败");
      }
    }
  }, [latestVersion, projectId, docId, cleaningJobId, fetchVersions, fetchSections]);

  // 版本冲突/租约丢失时编辑器只读。
  const editorReadOnly =
    workbench.submitting || workbench.acquiringLease || workbench.leaseLost || workbench.conflictState !== null || !workbench.lease;

  // dirty guard：切换章节/来源前确认。
  const guardedSwitchSection = useCallback((sectionId: string) => {
    if (workbench.submitting) return;
    if (workbench.isDirty) {
      setGuardPrompt({
        pendingTarget: () => workbench.switchSection(sectionId),
      });
    } else {
      workbench.switchSection(sectionId);
    }
  }, [workbench]);

  const selectedSection = workbench.selectedSection;
  const selectedSectionId = workbench.selectedSectionId;

  // 切换清洗来源：仅当有当前脏内容且持有租约时提示。
  const guardedSwitchCleaningContext = useCallback((id: string) => {
    if (workbench.submitting) return;
    if (workbench.isDirty) {
      setGuardPrompt({ pendingTarget: () => switchCleaningContext(id) });
    } else {
      switchCleaningContext(id);
    }
  }, [workbench, switchCleaningContext]);

  const [loadingVersionInProgress, setLoadingVersionInProgress] = useState<string | null>(null);
  const viewFullText = useCallback(async (vid: string) => {
    setLoadingVersionInProgress(vid);
    try {
      const ver = await api.get("/cleaned-versions/{vid}", { params: { vid } });
      const w = window.open("", "_blank");
      if (w) {
        w.document.write(`<pre style="white-space:pre-wrap;padding:16px;font-family:ui-monospace,monospace">${ver.merged_markdown.replace(/</g, "&lt;")}</pre>`);
        w.document.title = `合并版本 v${ver.version}`;
      }
    } catch {
      toast.error("查看全文失败");
    } finally {
      setLoadingVersionInProgress(null);
    }
  }, []);

  const handleCopyLocal = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(workbench.editedMarkdown);
      toast.success("已复制本地内容");
    } catch {
      toast.error("复制失败");
    }
  }, [workbench.editedMarkdown]);

  if (loading || contextLoading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">加载中...</div>
      </div>
    );
  }

  if (!cleaningJobId || !activeCleaningJob) {
    return (
      <div className="p-6">
        <Link
          href={`/projects/${projectId}/documents/${docId}`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3" />
          返回文档并选择解析结果
        </Link>
        <div className="py-12 text-center text-sm text-muted-foreground">
          尚未选择可进入的清洗来源，请先从解析结果创建或进入清洗任务。
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* dirty guard 对话框 */}
      <Dialog
        open={guardPrompt !== null}
        onOpenChange={(open) => {
          if (!open && !workbench.saving) setGuardPrompt(null);
        }}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>有未保存的修改</DialogTitle>
            <DialogDescription>
              当前章节存在未保存的文本。保存后可保留修改；放弃会丢失本地文本。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setGuardPrompt(null);
              }}
              disabled={workbench.saving}
            >
              留在当前页
            </Button>
            <Button
              variant="outline"
              onClick={async () => {
                const pending = guardPrompt?.pendingTarget;
                const saved = await workbench.save();
                if (saved.ok) {
                  setGuardPrompt(null);
                  if (pending) pending();
                }
              }}
              disabled={workbench.saving}
            >
              <SaveIcon className="size-3" />
              保存并继续
            </Button>
            <Button
              variant="destructive"
              disabled={workbench.saving}
              onClick={() => {
                const pending = guardPrompt?.pendingTarget;
                setGuardPrompt(null);
                workbench.resetForCleanup();
                if (pending) pending();
              }}
            >
              放弃
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 版本冲突对话框 */}
      <Dialog
        open={workbench.conflictState !== null}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>内容已被他人更新</DialogTitle>
            <DialogDescription>
              你的本地文本已保留，不会被自动覆盖。可以重新载入服务端最新版本，或复制本地内容。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                void handleCopyLocal();
              }}
            >
              <ClipboardIcon className="size-3" />
              复制本地内容
            </Button>
            <Button
              onClick={() => {
                void workbench.reloadFromServer();
              }}
            >
              <RefreshCcwIcon className="size-3" />
              重新载入
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Section sidebar */}
      {sidebarOpen && (
        <div className="w-56 shrink-0 border-r flex flex-col">
          <div className="p-2 border-b flex items-center justify-between">
            <Link
              href={`/projects/${projectId}/documents/${docId}`}
              onClick={(event) => {
                if (workbench.submitting) {
                  event.preventDefault();
                } else if (workbench.isDirty) {
                  event.preventDefault();
                  setGuardPrompt({ pendingTarget: () => router.push(`/projects/${projectId}/documents/${docId}`) });
                }
              }}
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
                      {m.user_id.slice(0, 8)} ({m.role})
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
                      onClick={() => guardedSwitchSection(section.id)}
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
                        {assignee && <span className="text-muted-foreground">{assignee.user_id.slice(0, 8)}</span>}
                      </div>
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
          <span className="text-xs text-muted-foreground">解析来源</span>
          <select
            className="max-w-64 rounded border px-2 py-1 text-xs bg-transparent"
            value={activeCleaningJob.id}
            onChange={(event) => guardedSwitchCleaningContext(event.target.value)}
          >
            {cleaningJobs.map((job) => (
              <option key={job.id} value={job.id}>
                {job.parser_profile_name ?? "未知解析器"} · 解析 {job.parse_completed_at ? new Date(job.parse_completed_at).toLocaleString("zh-CN") : "处理中"} · 清洗 {new Date(job.created_at).toLocaleString("zh-CN")}
              </option>
            ))}
          </select>
          <StatusBadge
            status={activeCleaningJob.status}
            label={
              {
                queued: "准备工作台",
                processing: "生成章节中",
                completed: "工作台可用",
                failed: "初始化失败",
              }[activeCleaningJob.status] ?? activeCleaningJob.status
            }
            className="text-xs"
          />
          <div className="flex-1" />
          {workbench.leaseLost && (
            <>
              <Badge variant="outline" className="text-orange-600">租约已失效 · 只读</Badge>
              <Button variant="outline" size="sm" onClick={() => void workbench.relock()} disabled={workbench.acquiringLease}>
                <RefreshCcwIcon className="size-3" />
                重新获取租约
              </Button>
              {workbench.isDirty && (
                <Button variant="outline" size="sm" onClick={handleCopyLocal}>
                  <ClipboardIcon className="size-3" />
                  复制草稿
                </Button>
              )}
            </>
          )}
          {editorReadOnly && !workbench.leaseLost && (
            <Badge variant="outline" className="text-muted-foreground">只读</Badge>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={workbench.saving || editorReadOnly || !workbench.isDirty}
          >
            <SaveIcon className="size-3" />
            {workbench.saving ? "保存中..." : "保存"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleSubmitReview}
            disabled={workbench.submitting || editorReadOnly}
          >
            <SendIcon className="size-3" />
            {workbench.submitting ? "提交中..." : "提交审核"}
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
              disabled={completionStats.completed === 0 || merging}
            >
              {merging ? "合并中..." : "生成合并版本"}
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
                  <button
                    className="text-xs text-blue-600 underline"
                    onClick={() => void viewFullText(latestVersion.id)}
                    disabled={loadingVersionInProgress === latestVersion.id}
                  >
                    {loadingVersionInProgress === latestVersion.id ? "加载中..." : "查看全文"}
                  </button>
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
              ) : pdfError ? (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  PDF 加载失败（资源不存在或无权访问）
                </div>
              ) : (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  PDF 加载中...
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
              <div className="markdown-preview p-3">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath, remarkSoftLineBreaks]}
                  rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
                  components={{
                    ol: ({ node, start, ...props }) => {
                      void node;
                      const s = typeof start === "number" ? start : undefined;
                      return <ol start={s} style={s && s > 1 ? { counterReset: `list-item ${s - 1}` } : undefined} {...props} />;
                    },
                  }}
                >
                  {previewMarkdown || "暂无内容"}
                </ReactMarkdown>
              </div>
            </ScrollArea>
          </div>

          {/* Column 3: Editor + Comments */}
          <div className="flex flex-col min-h-0">
            <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50">
              Markdown 编辑器
              {selectedSection && (
                <span className="ml-2 text-[10px] text-muted-foreground">
                  rev {selectedSection.content_revision ?? 0}
                </span>
              )}
            </div>
            <div className="flex-1 min-h-0 overflow-auto">
              {workbench.acquiringLease ? (
                <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                  获取编辑租约中...
                </div>
              ) : (
                <CodeMirrorEditor
                  value={workbench.editedMarkdown}
                  onChange={workbench.setEditedMarkdown}
                  readOnly={editorReadOnly}
                />
              )}
            </div>
            {/* Comments panel */}
            <div className="h-48 shrink-0 border-t flex flex-col">
              <div className="px-2 py-1 border-b text-xs font-medium text-muted-foreground bg-muted/50 flex items-center gap-1">
                <MessageSquareIcon className="size-3" />
                评论
              </div>
              <ScrollArea className="flex-1">
                <div className="p-2 space-y-2">
                  {workbench.comments.map((c) => (
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
                  {workbench.comments.length === 0 && (
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
