"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { useWs } from "@/hooks/use-ws";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  FileTextIcon,
  PlayIcon,
  Loader2Icon,
  ArrowLeftIcon,
  Trash2Icon,
} from "lucide-react";

interface DocumentDetail {
  id: string;
  filename: string;
  status: string;
  file_size: number;
  page_count?: number;
  mime_type?: string;
  storage_key?: string;
  uploaded_by?: string;
  created_at: string;
  updated_at: string;
}

interface ParseJob {
  id: string;
  status: string;
  parser_profile_id?: string;
  started_at?: string;
  completed_at?: string;
  error_message?: string;
  created_at: string;
}

interface ProfileOption {
  id: string;
  name: string;
  is_default: boolean;
}

interface TaskProgress {
  task_id: string;
  task_type: string;
  status: string;
  progress: number | null;
  entity_id: string;
}

export default function DocumentDetailPage() {
  const params = useParams<{ id: string; did: string }>();
  const router = useRouter();
  const projectId = params.id;
  const docId = params.did;
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [parseJobs, setParseJobs] = useState<ParseJob[]>([]);
  const [parserProfiles, setParserProfiles] = useState<ProfileOption[]>([]);
  const [chunkProfiles, setChunkProfiles] = useState<ProfileOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [taskProgress, setTaskProgress] = useState<TaskProgress | null>(null);
  const [deleteJobId, setDeleteJobId] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [showCleanPicker, setShowCleanPicker] = useState(false);
  const [selectedCleanJobId, setSelectedCleanJobId] = useState<string>("");
  const initialLoadDone = useRef(false);

  // Fetch all data; silent=true skips the loading spinner (used for WS refreshes)
  const fetchData = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    Promise.all([
      api.get<DocumentDetail>(
        `/projects/${projectId}/documents/${docId}`
      ),
      api
        .get<ParseJob[]>(
          `/projects/${projectId}/documents/${docId}/parse-jobs`
        )
        .catch(() => [] as ParseJob[]),
      api
        .get<{ items: ProfileOption[] }>(
          `/projects/${projectId}/parser-profiles?page=1&page_size=50`
        )
        .catch(() => ({ items: [] })),
      api
        .get<{ items: ProfileOption[] }>(
          `/projects/${projectId}/chunk-profiles?page=1&page_size=50`
        )
        .catch(() => ({ items: [] })),
    ])
      .then(([docData, jobsData, parserData, chunkData]) => {
        setDoc(docData);
        setParseJobs(jobsData);
        setParserProfiles(parserData.items);
        setChunkProfiles(chunkData.items);
        initialLoadDone.current = true;
      })
      .catch(() => toast.error("加载文档详情失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Subscribe to WebSocket for real-time task updates
  const { subscribe } = useWs();
  useEffect(() => {
    const unsub = subscribe("*", (msg: unknown) => {
      const data = msg as TaskProgress;
      // Track progress for parse tasks targeting this document
      if (data.entity_id === docId && data.task_type === "parse") {
        setTaskProgress({ ...data });
      }
      // Silent refresh to pick up DB changes (new job record, status updates)
      fetchData(true);
    });
    return unsub;
  }, [subscribe, fetchData, docId]);

  const getDefaultProfile = (profiles: ProfileOption[]) =>
    profiles.find((p) => p.is_default) || profiles[0];

  const [selectedParserId, setSelectedParserId] = useState<string>("");

  // Set default parser when profiles load
  useEffect(() => {
    if (parserProfiles.length > 0 && !selectedParserId) {
      const def = getDefaultProfile(parserProfiles);
      if (def) setSelectedParserId(def.id);
    }
  }, [parserProfiles, selectedParserId]);

  const handleParse = useCallback(async () => {
    if (!selectedParserId) {
      toast.error("请选择解析器");
      return;
    }
    setActionLoading("parse");
    try {
      await api.post(
        `/projects/${projectId}/documents/${docId}/parse`,
        { parser_profile_id: selectedParserId }
      );
      toast.success("解析任务已发起");
      setTaskProgress(null);
      fetchData(true);
    } catch {
      toast.error("发起解析失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, selectedParserId, fetchData]);

  const handleChunk = useCallback(async () => {
    const profile = getDefaultProfile(chunkProfiles);
    if (!profile) {
      toast.error("请先在设置中创建切分配置");
      return;
    }
    setActionLoading("chunk");
    try {
      await api.post(
        `/projects/${projectId}/documents/${docId}/chunk`,
        { chunk_profile_id: profile.id }
      );
      toast.success("切分任务已发起");
      fetchData(true);
    } catch {
      toast.error("发起切分失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, chunkProfiles, fetchData]);

  const startCleanAndNavigate = useCallback(async (parseJobId?: string) => {
    const cleanUrl = `/projects/${projectId}/documents/${docId}/clean`;
    setActionLoading("clean");
    try {
      await api.post(
        `/projects/${projectId}/documents/${docId}/cleaning/start`,
        parseJobId ? { parse_job_id: parseJobId } : undefined
      );
      toast.success("清洗任务已发起，正在跳转...");
      router.push(cleanUrl);
    } catch {
      toast.error("发起清洗失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, router]);

  const handleClean = useCallback(() => {
    const cleanUrl = `/projects/${projectId}/documents/${docId}/clean`;
    // Already in cleaning/cleaned state — go directly to workbench
    if (doc && ["cleaning", "cleaned"].includes(doc.status)) {
      router.push(cleanUrl);
      return;
    }
    // Check completed parse jobs
    const completedJobs = parseJobs.filter((j) => j.status === "completed");
    if (completedJobs.length === 0) {
      toast.error("没有已完成的解析记录，请先解析文档");
      return;
    }
    if (completedJobs.length === 1) {
      // Only one — use it directly
      startCleanAndNavigate(completedJobs[0].id);
      return;
    }
    // Multiple — let user choose
    setSelectedCleanJobId(completedJobs[0].id);
    setShowCleanPicker(true);
  }, [projectId, docId, doc, parseJobs, router, startCleanAndNavigate]);

  const handleDeleteJob = useCallback(async () => {
    if (!deleteJobId) return;
    setDeleteLoading(true);
    try {
      await api.delete(
        `/projects/${projectId}/documents/${docId}/parse-jobs/${deleteJobId}`
      );
      toast.success("解析记录已删除");
      setDeleteJobId(null);
      fetchData(true);
    } catch {
      toast.error("删除失败");
    } finally {
      setDeleteLoading(false);
    }
  }, [projectId, docId, deleteJobId, fetchData]);

  // Determine real-time progress for active parse tasks
  const activeProgress = taskProgress?.entity_id === docId && taskProgress?.task_type === "parse"
    ? taskProgress
    : null;

  const jobColumns: ColumnDef<ParseJob>[] = [
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "progress",
      header: "进度",
      render: (row) => {
        // Show real-time progress for active (non-terminal) jobs
        const isActive = row.status === "queued" || row.status === "processing";
        const pct = isActive && activeProgress
          ? (activeProgress.progress ?? 0)
          : row.status === "completed"
            ? 100
            : row.status === "failed"
              ? 0
              : 0;
        if (row.status === "completed") {
          return <span className="text-xs text-green-600 font-medium">100%</span>;
        }
        if (row.status === "failed") {
          return <span className="text-xs text-destructive font-medium">失败</span>;
        }
        return (
          <div className="flex items-center gap-2 min-w-[120px]">
            <Progress value={pct} className="h-2 flex-1" />
            <span className="text-xs text-muted-foreground w-8">{pct}%</span>
          </div>
        );
      },
    },
    {
      key: "parser_profile_id",
      header: "解析器",
      render: (row) => {
        const p = parserProfiles.find((pp) => pp.id === row.parser_profile_id);
        return p?.name ?? row.parser_profile_id?.slice(0, 8) ?? "-";
      },
    },
    {
      key: "started_at",
      header: "开始时间",
      render: (row) =>
        row.started_at
          ? new Date(row.started_at).toLocaleString("zh-CN")
          : "-",
    },
    {
      key: "completed_at",
      header: "完成时间",
      render: (row) =>
        row.completed_at
          ? new Date(row.completed_at).toLocaleString("zh-CN")
          : "-",
    },
    {
      key: "error",
      header: "错误信息",
      render: (row) =>
        row.error_message ? (
          <span className="text-xs text-destructive">
            {row.error_message}
          </span>
        ) : (
          "-"
        ),
    },
    {
      key: "actions",
      header: "",
      render: (row) => {
        const isActive = row.status === "queued" || row.status === "processing";
        return (
          <Button
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground hover:text-destructive"
            disabled={isActive}
            title={isActive ? "任务进行中，无法删除" : "删除解析记录"}
            onClick={() => setDeleteJobId(row.id)}
          >
            <Trash2Icon className="size-4" />
          </Button>
        );
      },
    },
  ];

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          文档不存在
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <Link
          href={`/projects/${projectId}/documents`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
        >
          <ArrowLeftIcon className="size-3" />
          返回文档列表
        </Link>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <FileTextIcon className="size-6" />
            <div>
              <h1 className="text-2xl font-semibold">{doc.filename}</h1>
              <div className="flex items-center gap-2 mt-1">
                <StatusBadge status={doc.status} />
                <span className="text-sm text-muted-foreground">
                  上传于{" "}
                  {new Date(doc.created_at).toLocaleString("zh-CN")}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Metadata */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>文档信息</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
            <div>
              <div className="text-muted-foreground">文件大小</div>
              <div className="font-medium">
                {doc.file_size < 1024 * 1024
                  ? `${(doc.file_size / 1024).toFixed(1)} KB`
                  : `${(doc.file_size / (1024 * 1024)).toFixed(1)} MB`}
              </div>
            </div>
            <div>
              <div className="text-muted-foreground">页数</div>
              <div className="font-medium">{doc.page_count ?? "-"}</div>
            </div>
            <div>
              <div className="text-muted-foreground">MIME类型</div>
              <div className="font-medium">{doc.mime_type ?? "-"}</div>
            </div>
            <div>
              <div className="text-muted-foreground">更新时间</div>
              <div className="font-medium">
                {new Date(doc.updated_at).toLocaleString("zh-CN")}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Action buttons */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>操作</CardTitle>
          <CardDescription>对文档执行各阶段处理操作</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            <div className="flex items-center gap-1">
              <select
                className="h-9 rounded-l-md border border-r-0 px-3 text-sm bg-transparent"
                value={selectedParserId}
                onChange={(e) => setSelectedParserId(e.target.value)}
                disabled={actionLoading !== null}
              >
                {parserProfiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <Button
                className="rounded-l-none"
                onClick={handleParse}
                disabled={actionLoading !== null || !selectedParserId}
              >
                {actionLoading === "parse" ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <PlayIcon className="size-4" />
                )}
                发起解析
              </Button>
            </div>
            <Button
              variant="outline"
              onClick={handleClean}
              disabled={actionLoading !== null || !["parsed", "cleaning", "cleaned"].includes(doc.status)}
            >
              {actionLoading === "clean" && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              文档清洗
            </Button>
            <Button
              variant="outline"
              onClick={handleChunk}
              disabled={actionLoading !== null || !["cleaning", "cleaned"].includes(doc.status)}
            >
              {actionLoading === "chunk" && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              执行切分
              {chunkProfiles.length > 0 && (
                <span className="text-xs opacity-70 ml-1">
                  ({getDefaultProfile(chunkProfiles)?.name})
                </span>
              )}
            </Button>
            <Button
              variant="outline"
              onClick={() => {}} // TODO: batch generate needs template + model selection
              disabled={actionLoading !== null || doc.status !== "chunked"}
            >
              {actionLoading === "generate" && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              批量生成
            </Button>
            <Link
              href={`/projects/${projectId}/documents/${docId}/chunks`}
            >
              <Button variant="outline">查看分块</Button>
            </Link>
          </div>
        </CardContent>
      </Card>

      {/* Parse jobs */}
      <Card>
        <CardHeader>
          <CardTitle>解析任务记录</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={jobColumns}
            data={parseJobs}
            total={parseJobs.length}
            page={1}
            pageSize={50}
            onPageChange={() => {}}
            rowKey={(row) => row.id}
          />
        </CardContent>
      </Card>

      {/* Delete parse job confirmation dialog */}
      <Dialog open={deleteJobId !== null} onOpenChange={(open) => { if (!open) setDeleteJobId(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除解析记录</DialogTitle>
            <DialogDescription>
              删除后，该解析记录及其产出的 Markdown 文件将被永久移除，无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteJobId(null)} disabled={deleteLoading}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleDeleteJob} disabled={deleteLoading}>
              {deleteLoading && <Loader2Icon className="size-4 animate-spin" />}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Parse job picker for cleaning */}
      <Dialog open={showCleanPicker} onOpenChange={(open) => { if (!open) setShowCleanPicker(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>选择解析结果</DialogTitle>
            <DialogDescription>
              该文档有多条已完成的解析记录，请选择要基于哪条进行清洗。
            </DialogDescription>
          </DialogHeader>
          <select
            className="w-full rounded border px-3 py-2 text-sm bg-transparent"
            value={selectedCleanJobId}
            onChange={(e) => setSelectedCleanJobId(e.target.value)}
          >
            {parseJobs
              .filter((j) => j.status === "completed")
              .map((j) => {
                const p = parserProfiles.find((pp) => pp.id === j.parser_profile_id);
                const time = j.completed_at ? new Date(j.completed_at).toLocaleString("zh-CN") : "";
                return (
                  <option key={j.id} value={j.id}>
                    {p?.name ?? "未知解析器"} — {time}
                  </option>
                );
              })}
          </select>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCleanPicker(false)}>
              取消
            </Button>
            <Button
              onClick={() => {
                setShowCleanPicker(false);
                startCleanAndNavigate(selectedCleanJobId);
              }}
              disabled={!selectedCleanJobId || actionLoading !== null}
            >
              {actionLoading === "clean" && <Loader2Icon className="size-4 animate-spin" />}
              开始清洗
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
