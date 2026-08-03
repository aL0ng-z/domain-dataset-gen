"use client";

import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Pagination } from "@/components/pagination";
import { api, ApiErrorException, formatJsonPreview } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { Loader2Icon, PlusIcon, SearchIcon } from "lucide-react";

type CuratedItemSummary = components["schemas"]["CuratedItemSummaryResponse"];

export type ContainerType = "dataset" | "benchmark";

interface Props {
  /** "dataset" 或 "benchmark"，决定 eligible 端点、添加端点和资格说明。 */
  containerType: ContainerType;
  projectId: string;
  containerId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 添加成功回调（父页面刷新列表/eligible/count）。 */
  onAdded?: () => void;
}

const PAGE_SIZE = 20;

/**
 * Dataset/Benchmark 共用“添加条目/用例”对话框（任务卡 §6）：
 * - 调用对应 eligible endpoint，支持搜索、分页、空态、选中和单项提交。
 * - 提交期间禁用；成功后关闭并刷新；409 按稳定 code 展示原因并刷新 eligible 列表，
 *   不乐观插入重复行。
 * - Dataset/Benchmark 共用选择器/分页状态组件，但资格说明分别显示
 *   （避免把 partially_supported item 误示为 Benchmark 可用）。
 */
export function EligibleItemPicker({ containerType, projectId, containerId, open, onOpenChange, onAdded }: Props) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<CuratedItemSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [submittingId, setSubmittingId] = useState<string | null>(null);

  const eligiblePath =
    containerType === "dataset"
      ? "/projects/{pid}/datasets/{did}/eligible-items"
      : "/projects/{pid}/benchmarks/{bid}/eligible-items";
  const addPath =
    containerType === "dataset"
      ? "/projects/{pid}/datasets/{did}/items"
      : "/projects/{pid}/benchmarks/{bid}/cases";
  const addField = containerType === "dataset" ? "curated_item_id" : "curated_item_id";
  const eligibleLabel = containerType === "dataset" ? "添加条目" : "添加用例";
  const gateNote =
    containerType === "dataset"
      ? "仅已批准且含证据的知识条目可加入数据集。"
      : "仅已批准、含证据且 source Candidate 判定为 supported 的知识条目可加入基准集。";

  const fetchEligible = useCallback(() => {
    if (!open) return;
    setLoading(true);
    api
      .get(eligiblePath, {
        params:
          containerType === "dataset"
            ? { pid: projectId, did: containerId }
            : { pid: projectId, bid: containerId },
        query: { page, page_size: PAGE_SIZE, query: search || undefined },
      })
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => {
        setItems([]);
        setTotal(0);
        toast.error("加载可添加条目失败");
      })
      .finally(() => setLoading(false));
  }, [open, eligiblePath, containerType, projectId, containerId, page, search]);

  useEffect(() => {
    if (!open) return;
    // 延迟到下一事件循环，避免 effect 内同步 setState。
    const timer = setTimeout(fetchEligible, 0);
    return () => clearTimeout(timer);
  }, [fetchEligible, open]);

  // 打开时重置搜索/页码。
  useEffect(() => {
    if (open) {
      setQuery("");
      setSearch("");
      setPage(1);
    }
  }, [open]);

  const handleSubmit = useCallback(
    async (item: CuratedItemSummary) => {
      setSubmittingId(item.id);
      try {
        await api.post(
          addPath,
          { curated_item_id: item.id },
          {
            params:
              containerType === "dataset"
                ? { pid: projectId, did: containerId }
                : { pid: projectId, bid: containerId },
          },
        );
        toast.success(containerType === "dataset" ? "已添加条目" : "已添加用例");
        onOpenChange(false);
        onAdded?.();
      } catch (err) {
        const e = err as ApiErrorException;
        const code = e?.apiError?.code;
        if (code === "COMPOSITION_MEMBER_EXISTS") {
          toast.error("该条目已在容器中，已刷新列表");
          fetchEligible();
        } else if (code === "COMPOSITION_ITEM_INELIGIBLE") {
          toast.error("条目不满足资格（未批准/缺证据/非 supported）");
          fetchEligible();
        } else if (code === "COMPOSITION_NOT_DRAFT") {
          toast.error("容器已 finalize，无法添加");
        } else {
          toast.error(e?.apiError?.message || "添加失败");
          fetchEligible();
        }
      } finally {
        setSubmittingId(null);
      }
    },
    [addPath, containerId, containerType, fetchEligible, onAdded, onOpenChange, projectId],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{eligibleLabel}</DialogTitle>
          <DialogDescription>{gateNote}</DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              className="w-full rounded border border-input bg-transparent pl-8 pr-2 py-1.5 text-sm outline-none focus-visible:border-ring"
              placeholder="搜索标题/摘要"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setSearch(query);
                  setPage(1);
                }
              }}
            />
          </div>
          <Button
            variant="outline"
            onClick={() => {
              setSearch(query);
              setPage(1);
            }}
          >
            搜索
          </Button>
        </div>

        <div className="min-h-[12rem] max-h-72 overflow-auto border rounded">
          {loading ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin inline mr-2" />
              加载中...
            </div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              {search ? "没有匹配的可添加条目" : "没有可添加条目"}
            </div>
          ) : (
            items.map((item) => (
              <div
                key={item.id}
                className="flex items-start justify-between gap-3 border-b px-3 py-2 last:border-b-0 hover:bg-muted/50"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-medium truncate">
                      {formatJsonPreview(item.pinned_content).slice(0, 60)}
                    </span>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {item.item_type} · v{item.current_revision}
                  </div>
                </div>
                <Button
                  size="xs"
                  disabled={submittingId === item.id}
                  onClick={() => handleSubmit(item)}
                >
                  {submittingId === item.id ? (
                    <Loader2Icon className="size-3 animate-spin" />
                  ) : (
                    <PlusIcon className="size-3" />
                  )}
                  添加
                </Button>
              </div>
            ))
          )}
        </div>

        {total > 0 && (
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
