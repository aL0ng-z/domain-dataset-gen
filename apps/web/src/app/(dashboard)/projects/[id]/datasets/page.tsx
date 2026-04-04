"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api, type PaginatedResponse } from "@/lib/api";
import { PlusIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface Dataset {
  id: string;
  name: string;
  description?: string;
  item_count: number;
  status: string;
  created_at: string;
}

export default function DatasetsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");

  const fetchDatasets = useCallback(() => {
    setLoading(true);
    api
      .get<PaginatedResponse<Dataset>>(
        `/projects/${projectId}/datasets?page=${page}&page_size=${pageSize}`
      )
      .then((data) => {
        setDatasets(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载数据集列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize]);

  useEffect(() => {
    fetchDatasets();
  }, [fetchDatasets]);

  const handleCreate = useCallback(async () => {
    if (!formName.trim()) {
      toast.error("请输入数据集名称");
      return;
    }
    try {
      await api.post(`/projects/${projectId}/datasets`, {
        name: formName,
        description: formDesc,
      });
      toast.success("数据集创建成功");
      setDialogOpen(false);
      setFormName("");
      setFormDesc("");
      fetchDatasets();
    } catch {
      toast.error("创建数据集失败");
    }
  }, [projectId, formName, formDesc, fetchDatasets]);

  const columns: ColumnDef<Dataset>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/datasets/${row.id}`}
          className="font-medium text-primary hover:underline"
        >
          {row.name}
        </Link>
      ),
    },
    {
      key: "description",
      header: "描述",
      render: (row) => row.description || "-",
    },
    {
      key: "item_count",
      header: "条目数",
      render: (row) => row.item_count,
    },
    {
      key: "status",
      header: "状态",
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: "created_at",
      header: "创建时间",
      render: (row) =>
        new Date(row.created_at).toLocaleString("zh-CN"),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Link href={`/projects/${projectId}/datasets/${row.id}`}>
          <Button variant="ghost" size="xs">
            管理
          </Button>
        </Link>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">数据集</h1>
          <p className="text-sm text-muted-foreground mt-1">
            管理微调数据集
          </p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger render={<Button />}>
            <PlusIcon className="size-4" />
            新建数据集
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新建数据集</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium">名称</label>
                <Input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="数据集名称"
                />
              </div>
              <div>
                <label className="text-sm font-medium">描述</label>
                <Textarea
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="数据集描述（可选）"
                />
              </div>
            </div>
            <DialogFooter>
              <Button onClick={handleCreate}>创建</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={datasets}
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
