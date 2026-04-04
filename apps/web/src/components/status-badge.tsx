import React from "react";
import { Badge, type badgeVariants } from "@/components/ui/badge";
import type { VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

const STATUS_STYLES: Record<string, { variant: BadgeVariant; className: string; label?: string }> = {
  // Document / general states
  uploaded: { variant: "secondary", className: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300", label: "已上传" },
  parsing: { variant: "secondary", className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300", label: "解析中" },
  parsed: { variant: "secondary", className: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300", label: "已解析" },
  cleaning: { variant: "secondary", className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300", label: "清洗中" },
  cleaned: { variant: "secondary", className: "bg-teal-100 text-teal-700 dark:bg-teal-900 dark:text-teal-300", label: "已清洗" },
  chunking: { variant: "secondary", className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300", label: "分块中" },
  chunked: { variant: "secondary", className: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300", label: "已分块" },
  generating: { variant: "secondary", className: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300", label: "生成中" },
  generated: { variant: "secondary", className: "bg-violet-100 text-violet-700 dark:bg-violet-900 dark:text-violet-300", label: "已生成" },

  // Common statuses
  draft: { variant: "secondary", className: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400", label: "草稿" },
  pending: { variant: "secondary", className: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300", label: "待处理" },
  processing: { variant: "secondary", className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300", label: "处理中" },
  running: { variant: "secondary", className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300", label: "运行中" },
  completed: { variant: "secondary", className: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300", label: "已完成" },
  failed: { variant: "destructive", className: "", label: "失败" },
  cancelled: { variant: "secondary", className: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400", label: "已取消" },

  // Review statuses
  approved: { variant: "secondary", className: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300", label: "已通过" },
  rejected: { variant: "destructive", className: "", label: "已拒绝" },
  in_review: { variant: "secondary", className: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300", label: "审核中" },

  // Evidence judgments
  supported: { variant: "secondary", className: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300", label: "有依据" },
  partially_supported: { variant: "secondary", className: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300", label: "部分依据" },
  unsupported: { variant: "destructive", className: "", label: "无依据" },
  out_of_scope: { variant: "secondary", className: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400", label: "超范围" },

  // Section statuses
  raw: { variant: "secondary", className: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400", label: "原始" },
  locked: { variant: "secondary", className: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300", label: "已锁定" },
  verified: { variant: "secondary", className: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300", label: "已验证" },

  // Active
  active: { variant: "secondary", className: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300", label: "活跃" },
  inactive: { variant: "secondary", className: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400", label: "停用" },
};

interface StatusBadgeProps {
  status: string;
  label?: string;
  className?: string;
}

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const config = STATUS_STYLES[status] || {
    variant: "outline" as BadgeVariant,
    className: "",
  };
  const displayLabel = label || config.label || status;

  return (
    <Badge
      variant={config.variant}
      className={cn(config.className, className)}
    >
      {displayLabel}
    </Badge>
  );
}
