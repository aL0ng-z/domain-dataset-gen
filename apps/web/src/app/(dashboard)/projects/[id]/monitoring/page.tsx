"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { RefreshCwIcon } from "lucide-react";

interface MonitoringSummary {
  total_tokens: number;
  total_requests: number;
  total_errors: number;
  avg_latency_ms: number;
}

interface DailyTrend {
  date: string;
  tokens: number;
  requests: number;
  errors: number;
}

interface ModelUsage {
  model: string;
  tokens: number;
  requests: number;
}

interface TemplateUsage {
  template_name: string;
  tokens: number;
  requests: number;
}

interface TaskTypeUsage {
  task_type: string;
  tokens: number;
  requests: number;
}

interface MonitoringData {
  summary: MonitoringSummary;
  daily_trend: DailyTrend[];
  by_model: ModelUsage[];
  by_template: TemplateUsage[];
  by_task_type: TaskTypeUsage[];
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

/** Simple horizontal bar chart using colored divs */
function SimpleBarChart({
  data,
  labelKey,
  valueKey,
  color = "bg-primary",
}: {
  data: Record<string, unknown>[];
  labelKey: string;
  valueKey: string;
  color?: string;
}) {
  const maxVal = Math.max(
    ...data.map((d) => Number(d[valueKey]) || 0),
    1
  );

  return (
    <div className="space-y-2">
      {data.map((d, i) => {
        const val = Number(d[valueKey]) || 0;
        const pct = (val / maxVal) * 100;
        return (
          <div key={i} className="flex items-center gap-2 text-sm">
            <span className="w-28 truncate text-muted-foreground text-xs">
              {String(d[labelKey])}
            </span>
            <div className="flex-1 h-5 bg-muted rounded overflow-hidden">
              <div
                className={`h-full ${color} rounded transition-all`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="w-16 text-right text-xs tabular-nums">
              {formatNumber(val)}
            </span>
          </div>
        );
      })}
      {data.length === 0 && (
        <div className="text-sm text-muted-foreground text-center py-4">
          暂无数据
        </div>
      )}
    </div>
  );
}

/** Simple daily trend using vertical bars */
function DailyTrendChart({ data }: { data: DailyTrend[] }) {
  const maxTokens = Math.max(...data.map((d) => d.tokens), 1);

  return (
    <div className="flex items-end gap-1 h-40">
      {data.map((d, i) => {
        const pct = (d.tokens / maxTokens) * 100;
        return (
          <div
            key={i}
            className="flex-1 flex flex-col items-center gap-1"
          >
            <div className="w-full flex justify-center">
              <div
                className="w-full max-w-6 bg-primary rounded-t transition-all"
                style={{ height: `${pct}%`, minHeight: d.tokens > 0 ? "2px" : "0" }}
              />
            </div>
            <span className="text-[9px] text-muted-foreground -rotate-45 origin-center whitespace-nowrap">
              {d.date.slice(5)}
            </span>
          </div>
        );
      })}
      {data.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
          暂无数据
        </div>
      )}
    </div>
  );
}

export default function MonitoringPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [data, setData] = useState<MonitoringData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(() => {
    setLoading(true);
    api
      .get<MonitoringData>(
        `/projects/${projectId}/monitoring/llm-usage`
      )
      .then(setData)
      .catch(() => toast.error("加载监控数据失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      </div>
    );
  }

  const summary = data?.summary || {
    total_tokens: 0,
    total_requests: 0,
    total_errors: 0,
    avg_latency_ms: 0,
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">LLM 用量监控</h1>
          <p className="text-sm text-muted-foreground mt-1">
            查看LLM调用统计和资源使用情况
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchData}>
          <RefreshCwIcon className="size-3" />
          刷新
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 mb-6 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-normal">
              总Token数
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatNumber(summary.total_tokens)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-normal">
              总请求数
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatNumber(summary.total_requests)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-normal">
              总错误数
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-destructive">
              {formatNumber(summary.total_errors)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground font-normal">
              平均延迟
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {summary.avg_latency_ms.toFixed(0)} ms
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Daily trend */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>每日Token用量趋势</CardTitle>
        </CardHeader>
        <CardContent>
          <DailyTrendChart data={data?.daily_trend || []} />
        </CardContent>
      </Card>

      {/* Breakdown charts */}
      <div className="grid gap-6 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>按模型</CardTitle>
          </CardHeader>
          <CardContent>
            <SimpleBarChart
              data={(data?.by_model || []) as unknown as Record<string, unknown>[]}
              labelKey="model"
              valueKey="tokens"
              color="bg-blue-500"
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>按模板</CardTitle>
          </CardHeader>
          <CardContent>
            <SimpleBarChart
              data={(data?.by_template || []) as unknown as Record<string, unknown>[]}
              labelKey="template_name"
              valueKey="tokens"
              color="bg-violet-500"
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>按任务类型</CardTitle>
          </CardHeader>
          <CardContent>
            <SimpleBarChart
              data={(data?.by_task_type || []) as unknown as Record<string, unknown>[]}
              labelKey="task_type"
              valueKey="tokens"
              color="bg-emerald-500"
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
