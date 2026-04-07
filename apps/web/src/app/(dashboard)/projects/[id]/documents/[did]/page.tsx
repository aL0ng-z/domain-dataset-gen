"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
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
import { api } from "@/lib/api";
import { useWs } from "@/hooks/use-ws";
import {
  FileTextIcon,
  PlayIcon,
  Loader2Icon,
  ArrowLeftIcon,
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

export default function DocumentDetailPage() {
  const params = useParams<{ id: string; did: string }>();
  const projectId = params.id;
  const docId = params.did;
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [parseJobs, setParseJobs] = useState<ParseJob[]>([]);
  const [parserProfiles, setParserProfiles] = useState<ProfileOption[]>([]);
  const [chunkProfiles, setChunkProfiles] = useState<ProfileOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    setLoading(true);
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
    const unsub = subscribe("*", () => {
      fetchData();
    });
    return unsub;
  }, [subscribe, fetchData]);

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
      // Refresh immediately to show the new parse job, then again after a delay
      fetchData();
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
      fetchData();
    } catch {
      toast.error("发起切分失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, chunkProfiles, fetchData]);

  const handleCleanStart = useCallback(async () => {
    setActionLoading("clean");
    try {
      await api.post(
        `/projects/${projectId}/documents/${docId}/cleaning/start`
      );
      toast.success("清洗任务已发起");
      fetchData();
    } catch {
      toast.error("发起清洗失败");
    } finally {
      setActionLoading(null);
    }
  }, [projectId, docId, fetchData]);

  const jobColumns: ColumnDef<ParseJob>[] = [
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
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
              onClick={handleCleanStart}
              disabled={actionLoading !== null || doc.status !== "parsed"}
            >
              {actionLoading === "clean" && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              开始清洗
            </Button>
            <Link
              href={`/projects/${projectId}/documents/${docId}/clean`}
            >
              <Button variant="outline" disabled={!["cleaning", "cleaned"].includes(doc.status)}>
                清洗工作台
              </Button>
            </Link>
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
    </div>
  );
}
