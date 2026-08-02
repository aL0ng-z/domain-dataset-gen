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
import { api, ApiErrorException } from "@/lib/api";
import type { components } from "@/lib/api/generated";
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

type DocumentDetail = components["schemas"]["DocumentResponse"];
type ParseJob = components["schemas"]["ParseJobResponse"];
type ProfileOption = components["schemas"]["ParserProfileResponse"] | components["schemas"]["ChunkProfileResponse"];
type CleaningJobContext = components["schemas"]["CleaningJobResponse"];

export default function DocumentDetailPage() {
  const params = useParams<{ id: string; did: string }>();
  const router = useRouter();
  const projectId = params.id;
  const docId = params.did;
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [parseJobs, setParseJobs] = useState<ParseJob[]>([]);  const [cleaningJobs, setCleaningJobs] = useState<CleaningJobContext[]>([]);
  const [parserProfiles, setParserProfiles] = useState<ProfileOption[]>([]);
  const [chunkProfiles, setChunkProfiles] = useState<ProfileOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [deleteJobId, setDeleteJobId] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [showCleanPicker, setShowCleanPicker] = useState(false);
  const [selectedCleanJobId, setSelectedCleanJobId] = useState<string>("");
  const initialLoadDone = useRef(false);
  // T06 §6：切分 idempotency key 由一次用户操作生成并复用，网络重试不换 key。
  const chunkIdempotencyRef = useRef<string | null>(null);
  const [chunkSets, setChunkSets] = useState<components["schemas"]["ChunkSetSummary"][]>([]);

  // Fetch all data; silent=true skips the loading spinner (used for WS refreshes)
  const fetchData = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    const base = { pid: projectId, did: docId };
    Promise.all([
      api.get("/projects/{pid}/documents/{did}", { params: base }),
      api
        .get("/projects/{pid}/documents/{did}/parse-jobs", { params: base })
        .catch(() => [] as ParseJob[]),
      api
        .get("/projects/{pid}/documents/{did}/cleaning-jobs", { params: base })
        .catch(() => [] as CleaningJobContext[]),
      api
        .get("/projects/{pid}/parser-profiles/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 50 },
        })
        .catch(() => ({ items: [] as ProfileOption[] })),
      api
        .get("/projects/{pid}/chunk-profiles/", {
          params: { pid: projectId },
          query: { page: 1, page_size: 50 },
        })
        .catch(() => ({ items: [] as ProfileOption[] })),
      api
        .get("/projects/{pid}/documents/{did}/chunk-sets", {
          params: base,
          query: { page: 1, page_size: 20 },
        })
        .catch(() => ({ items: [] as components["schemas"]["ChunkSetSummary"][], total: 0, page: 1, page_size: 20 })),
    ])
      .then(([docData, jobsData, cleanJobsData, parserData, chunkData, chunkSetData]) => {
        setDoc(docData);
        setParseJobs(jobsData);
        setCleaningJobs(cleanJobsData);
        setParserProfiles(parserData.items);
        setChunkProfiles(chunkData.items);
        setChunkSets(chunkSetData.items);
        initialLoadDone.current = true;
      })
      .catch(() => toast.error("加载文档详情失败"))
      .finally(() => setLoading(false));
  }, [projectId, docId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Subscribe to WebSocket for real-time task updates
  const { lastMessage } = useWs();
  useEffect(() => {
    if (lastMessage) {
      // Refresh persisted states instead of presenting parser milestones as
      // a measurable percentage; other document task updates remain visible.
      fetchData(true);
    }
  }, [lastMessage, fetchData]);

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
      await api.post("/projects/{pid}/documents/{did}/parse", {
        parser_profile_id: selectedParserId,
      }, {
        params: { pid: projectId, did: docId },
      });
      toast.success("解析任务已发起");
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
    // T06 §6：一次用户操作生成并复用一个 idempotency key；网络重试不得换 key。
    const idemKey = chunkIdempotencyRef.current ?? crypto.randomUUID();
    chunkIdempotencyRef.current = idemKey;
    try {
      const result = await api.post("/projects/{pid}/documents/{did}/chunk", {
        chunk_profile_id: profile.id,
      }, {
        params: { pid: projectId, did: docId },
        headers: { "Idempotency-Key": idemKey },
      });
      toast.success(result.reused ? "复用既有切分任务" : "切分任务已发起");
      fetchData(true);
    } catch (e) {
      // 409 后刷新服务端状态（版本/活跃 set/失败原因）。
      if (e instanceof ApiErrorException && e.apiError.status === 409) {
        fetchData(true);
      }
      toast.error("发起切分失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, chunkProfiles, fetchData]);

  const cleanUrlFor = useCallback((cleaningJobId: string) => (
    `/projects/${projectId}/documents/${docId}/clean?cleaning_job_id=${encodeURIComponent(cleaningJobId)}`
  ), [projectId, docId]);

  const startCleanAndNavigate = useCallback(async (parseJobId: string) => {
    setActionLoading("clean");
    try {
      const result = await api.post("/projects/{pid}/documents/{did}/cleaning/start", {
        parse_job_id: parseJobId,
      }, {
        params: { pid: projectId, did: docId },
      });
      toast.success(result.reused ? "正在进入已有清洗工作台" : "清洗任务已发起，正在进入工作台");
      router.push(cleanUrlFor(result.cleaning_job_id));
    } catch {
      toast.error("发起清洗失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, router, cleanUrlFor]);

  const openCleanSourcePicker = useCallback((parseJobId?: string) => {
    const completedJobs = parseJobs.filter((job) => job.status === "completed");
    if (completedJobs.length === 0) {
      toast.error("没有已完成的解析记录，请先解析文档");
      return;
    }
    setSelectedCleanJobId(parseJobId ?? completedJobs[0].id);
    setShowCleanPicker(true);
  }, [parseJobs]);

  const handleClean = useCallback(() => {
    openCleanSourcePicker();
  }, [openCleanSourcePicker]);

  const handleDeleteJob = useCallback(async () => {
    if (!deleteJobId) return;
    setDeleteLoading(true);
    try {
      await api.delete("/projects/{pid}/documents/{did}/parse-jobs/{jid}", {
        params: { pid: projectId, did: docId, jid: deleteJobId },
      });
      toast.success("解析记录已删除");
      setDeleteJobId(null);
      fetchData(true);
    } catch {
      toast.error("删除失败");
    } finally {
      setDeleteLoading(false);
    }
  }, [projectId, docId, deleteJobId, fetchData]);

  const completedParseJobs = parseJobs.filter((job) => job.status === "completed");
  const selectedCleanJob = parseJobs.find((job) => job.id === selectedCleanJobId);
  const cleaningJobForParse = (parseJobId: string) => cleaningJobs.find(
    (job) => job.parse_job_id === parseJobId && ["queued", "processing", "completed"].includes(job.status)
  );
  const selectedCleaningContext = selectedCleanJob ? cleaningJobForParse(selectedCleanJob.id) : undefined;

  const jobColumns: ColumnDef<ParseJob>[] = [
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "stage",
      header: "执行情况",
      render: (row) => {
        const text = {
          queued: "等待解析",
          processing: "解析处理中，耗时取决于文档与解析器",
          completed: "解析结果已生成",
          failed: "解析未完成",
        }[row.status] ?? row.status;
        return <span className="text-xs text-muted-foreground">{text}</span>;
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
        const existingContext = cleaningJobForParse(row.id);
        return (
          <div className="flex items-center justify-end gap-2">
            {row.status === "completed" && (
              <Button
                variant="outline"
                size="xs"
                disabled={actionLoading !== null}
                onClick={() => {
                  if (existingContext) {
                    router.push(cleanUrlFor(existingContext.id));
                  } else {
                    openCleanSourcePicker(row.id);
                  }
                }}
              >
                进入此解析结果
              </Button>
            )}
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
          </div>
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
              <div className="text-muted-foreground">SHA256</div>
              <div className="font-mono text-xs truncate max-w-40">
                {doc.sha256.slice(0, 16)}…
              </div>
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
          <CardDescription>解析完成后，请选择明确的解析结果作为清洗来源</CardDescription>
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
              disabled={actionLoading !== null || completedParseJobs.length === 0}
            >
              {actionLoading === "clean" && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              选择解析结果进行清洗
            </Button>
            <Button
              variant="outline"
              onClick={handleChunk}
              disabled={
                actionLoading !== null ||
                !["cleaning", "cleaned"].includes(doc.status) ||
                chunkSets.some((cs) => cs.status === "pending" || cs.status === "processing")
              }
              title={
                chunkSets.some((cs) => cs.status === "pending" || cs.status === "processing")
                  ? "该文档已有切分任务进行中"
                  : undefined
              }
            >
              {actionLoading === "chunk" && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              执行切分
              {chunkSets.some((cs) => cs.status === "pending" || cs.status === "processing") && (
                <span className="text-xs opacity-70 ml-1">
                  (切分中)
                </span>
              )}
              {chunkProfiles.length > 0 && !chunkSets.some((cs) => cs.status === "pending" || cs.status === "processing") && (
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
          <CardDescription>
            解析任务展示处理状态，不以估算百分比表示耗时进度。已完成结果可作为清洗任务来源。
          </CardDescription>
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
            <DialogTitle>确认清洗来源</DialogTitle>
            <DialogDescription>
              请选择要处理的解析结果。已有清洗工作台会直接进入，未开始的结果将创建独立清洗任务。
            </DialogDescription>
          </DialogHeader>
          <label className="text-sm font-medium" htmlFor="clean-source-job">
            已完成的解析结果
          </label>
          <select
            id="clean-source-job"
            className="w-full rounded border px-3 py-2 text-sm bg-transparent"
            value={selectedCleanJobId}
            onChange={(e) => setSelectedCleanJobId(e.target.value)}
          >
            {completedParseJobs.map((j) => {
                const p = parserProfiles.find((pp) => pp.id === j.parser_profile_id);
                const time = j.completed_at ? new Date(j.completed_at).toLocaleString("zh-CN") : "";
                return (
                  <option key={j.id} value={j.id}>
                    {p?.name ?? "未知解析器"} — {time}
                  </option>
                );
              })}
          </select>
          {selectedCleanJob && (
            <div className="rounded-md border bg-muted/30 p-3 text-sm">
              <div>
                <span className="text-muted-foreground">来源解析器：</span>
                {parserProfiles.find((profile) => profile.id === selectedCleanJob.parser_profile_id)?.name ?? "未知解析器"}
              </div>
              <div>
                <span className="text-muted-foreground">完成时间：</span>
                {selectedCleanJob.completed_at
                  ? new Date(selectedCleanJob.completed_at).toLocaleString("zh-CN")
                  : "-"}
              </div>
              <div>
                <span className="text-muted-foreground">解析记录：</span>
                <span className="font-mono text-xs">{selectedCleanJob.id}</span>
              </div>
              <div>
                <span className="text-muted-foreground">清洗工作台：</span>
                {selectedCleaningContext ? "已存在，可继续处理" : "尚未创建"}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCleanPicker(false)}>
              取消
            </Button>
            <Button
              onClick={() => {
                setShowCleanPicker(false);
                if (selectedCleaningContext) {
                  router.push(cleanUrlFor(selectedCleaningContext.id));
                } else {
                  startCleanAndNavigate(selectedCleanJobId);
                }
              }}
              disabled={!selectedCleanJobId || actionLoading !== null}
            >
              {actionLoading === "clean" && <Loader2Icon className="size-4 animate-spin" />}
              {selectedCleaningContext ? "进入此解析结果" : "开始清洗"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
