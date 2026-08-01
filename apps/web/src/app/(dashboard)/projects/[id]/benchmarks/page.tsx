"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { usePagination } from "@/hooks/use-pagination";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
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

type Benchmark = components["schemas"]["BenchmarkResponse"];

export default function BenchmarksPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { page, pageSize, setPage } = usePagination();
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");

  const fetchBenchmarks = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/benchmarks/", {
        params: { pid: projectId },
        query: { page, page_size: pageSize },
      })
      .then((data) => {
        setBenchmarks(data.items);
        setTotal(data.total);
      })
      .catch(() => toast.error("加载基准集列表失败"))
      .finally(() => setLoading(false));
  }, [projectId, page, pageSize]);

  useEffect(() => {


    // 延迟到下一事件循环再触发请求，避免在 effect 内同步 setState


    // （react-hooks/set-state-in-effect），并通过 cleanup 取消未完成的调度。


    const timer = setTimeout(fetchBenchmarks, 0);


    return () => clearTimeout(timer);


  }, [fetchBenchmarks]);

  const handleCreate = useCallback(async () => {
    if (!formName.trim()) {
      toast.error("请输入基准集名称");
      return;
    }
    try {
      await api.post("/projects/{pid}/benchmarks/", {
        name: formName,
        description: formDesc,
      }, {
        params: { pid: projectId },
      });
      toast.success("基准集创建成功");
      setDialogOpen(false);
      setFormName("");
      setFormDesc("");
      fetchBenchmarks();
    } catch {
      toast.error("创建基准集失败");
    }
  }, [projectId, formName, formDesc, fetchBenchmarks]);

  const columns: ColumnDef<Benchmark>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <Link
          href={`/projects/${projectId}/benchmarks/${row.id}`}
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
        <Link href={`/projects/${projectId}/benchmarks/${row.id}`}>
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
          <h1 className="text-2xl font-semibold">基准集</h1>
          <p className="text-sm text-muted-foreground mt-1">
            管理评测基准集
          </p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger render={<Button />}>
            <PlusIcon className="size-4" />
            新建基准集
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新建基准集</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium">名称</label>
                <Input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="基准集名称"
                />
              </div>
              <div>
                <label className="text-sm font-medium">描述</label>
                <Textarea
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="基准集描述（可选）"
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
          data={benchmarks}
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
