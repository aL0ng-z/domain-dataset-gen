"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, type PaginatedResponse } from "@/lib/api";
import { UploadIcon, FileTextIcon, Loader2Icon } from "lucide-react";

interface Document {
  id: string;
  filename: string;
  status: string;
  file_size: number;
  page_count?: number;
  uploaded_by?: string;
  created_at: string;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = useCallback(() => {
    setLoading(true);
    api
      .get<PaginatedResponse<Document>>(
        `/projects/${projectId}/documents?page=${page}&page_size=${pageSize}`
      )
      .then((data) => {
        setDocuments(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载文档列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  const handleUpload = useCallback(
    async (files: FileList | File[]) => {
      const fileArray = Array.from(files);
      if (fileArray.length === 0) return;

      setUploading(true);
      try {
        for (const file of fileArray) {
          const formData = new FormData();
          formData.append("file", file);
          await api.upload(`/projects/${projectId}/documents/upload`, formData);
        }
        toast.success(`成功上传 ${fileArray.length} 个文件`);
        fetchDocuments();
      } catch {
        toast.error("上传失败");
      } finally {
        setUploading(false);
      }
    },
    [projectId, fetchDocuments]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const files = e.dataTransfer.files;
      handleUpload(files);
    },
    [handleUpload]
  );

  const handleTriggerParse = useCallback(
    async (docId: string) => {
      try {
        await api.post(`/projects/${projectId}/documents/${docId}/parse`);
        toast.success("已发起解析任务");
        fetchDocuments();
      } catch {
        toast.error("发起解析失败");
      }
    },
    [projectId, fetchDocuments]
  );

  const columns: ColumnDef<Document>[] = [
    {
      key: "filename",
      header: "文件名",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/documents/${row.id}`}
          className="font-medium text-primary hover:underline"
        >
          {row.filename}
        </Link>
      ),
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "file_size",
      header: "文件大小",
      render: (row) => formatFileSize(row.file_size),
    },
    {
      key: "page_count",
      header: "页数",
      render: (row) => row.page_count ?? "-",
    },
    {
      key: "created_at",
      header: "上传时间",
      render: (row) =>
        new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <div className="flex gap-1">
          {row.status === "uploaded" && (
            <Button
              variant="outline"
              size="xs"
              onClick={() => handleTriggerParse(row.id)}
            >
              发起解析
            </Button>
          )}
          <Link href={`/projects/${projectId}/documents/${row.id}`}>
            <Button variant="ghost" size="xs">
              详情
            </Button>
          </Link>
        </div>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">文档管理</h1>
          <p className="text-sm text-muted-foreground mt-1">
            上传PDF文档并管理解析流程
          </p>
        </div>
        <Button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <UploadIcon className="size-4" />
          )}
          上传文档
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          multiple
          className="hidden"
          onChange={(e) => e.target.files && handleUpload(e.target.files)}
        />
      </div>

      {/* Drag-drop upload area */}
      <Card
        className={`mb-6 border-2 border-dashed transition-colors ${
          dragOver
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/25"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <CardContent className="flex flex-col items-center justify-center py-8">
          <FileTextIcon className="size-10 text-muted-foreground mb-2" />
          <p className="text-sm text-muted-foreground">
            拖拽PDF文件到此处上传，或点击上方按钮选择文件
          </p>
        </CardContent>
      </Card>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={documents}
          total={total}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          rowKey={(row) => row.id}
        />
      )}
    </div>
  );
}
