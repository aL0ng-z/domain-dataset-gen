"use client";

import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2Icon, PencilIcon, PlusIcon } from "lucide-react";
import { api } from "@/lib/api";
import { DataTable, type ColumnDef } from "@/components/data-table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

/**
 * ParserProfile 配置（T03 安全合同）：
 * - 项目用户不再填写 base_url/upload_url/results_url/vlm_base_url 或自由 JSON 网络字段；
 * - 先选 parser_name，再从服务端安全列表选择 endpoint_ref，并编辑允许的功能参数；
 * - UI 只展示「凭证已配置/未配置」「端点由管理员管理」，绝不展示真实 Token。
 */

interface ParserEndpoint {
  endpoint_ref: string;
  display_name: string;
  parser_name: string;
  credential_configured: boolean;
}

interface ParserProfileItem {
  id: string;
  name: string;
  parser_name: string;
  parser_options: Record<string, string | number | boolean> | null;
  is_default: boolean;
  version: number;
  requires_endpoint_remap?: boolean;
}

// 允许的功能参数编辑表单（白名单与后端 ALLOWED_OPTION_FIELDS 对齐）。
const FUNCTIONAL_FIELDS: Record<
  string,
  { key: string; label: string; type: "number" | "text" | "select" | "boolean"; options?: string[] }[]
> = {
  mineru: [
    { key: "model_version", label: "模型版本", type: "select", options: ["vlm", "ocr", "auto"] },
    { key: "enable_formula", label: "启用公式", type: "boolean" },
    { key: "enable_table", label: "启用表格", type: "boolean" },
    { key: "language", label: "语言", type: "text" },
    { key: "timeout_seconds", label: "超时(秒)", type: "number" },
  ],
  paddleocr: [
    { key: "use_doc_orientation_classify", label: "文档方向分类", type: "boolean" },
    { key: "use_doc_unwarping", label: "文档拉平", type: "boolean" },
    { key: "use_textline_orientation", label: "文本行方向", type: "boolean" },
    { key: "visualize", label: "可视化", type: "boolean" },
    { key: "timeout_seconds", label: "超时(秒)", type: "number" },
  ],
  mineru_local: [
    { key: "model_path", label: "本地模型目录", type: "text" },
    { key: "device_map", label: "推理设备", type: "select", options: ["auto", "mps", "cpu"] },
    { key: "render_dpi", label: "页面DPI", type: "number" },
    { key: "max_pages", label: "最大页数", type: "number" },
    { key: "image_analysis", label: "分析图片与图表", type: "boolean" },
  ],
  mineru_local_service: [
    { key: "backend", label: "服务推理路径", type: "select", options: ["vlm-auto-engine", "vlm-http-client"] },
    { key: "language", label: "语言", type: "text" },
    { key: "parse_method", label: "解析方式", type: "text" },
    { key: "formula_enable", label: "启用公式", type: "boolean" },
    { key: "table_enable", label: "启用表格", type: "boolean" },
    { key: "image_analysis", label: "分析图片与图表", type: "boolean" },
    { key: "timeout_seconds", label: "超时(秒)", type: "number" },
  ],
  paddleocr_local_service: [
    { key: "use_doc_orientation_classify", label: "文档方向分类", type: "boolean" },
    { key: "use_doc_unwarping", label: "文档拉平", type: "boolean" },
    { key: "use_textline_orientation", label: "文本行方向", type: "boolean" },
    { key: "use_layout_detection", label: "版面检测", type: "boolean" },
    { key: "visualize", label: "可视化", type: "boolean" },
    { key: "max_new_tokens", label: "最大新Token", type: "number" },
    { key: "parse_timeout_seconds", label: "解析超时(秒)", type: "number" },
  ],
  pymupdf4llm: [],
  mock: [],
};

const PARSER_LABELS: Record<string, string> = {
  pymupdf4llm: "PyMuPDF4LLM（本地）",
  mineru: "MinerU（API）",
  mineru_local: "MinerU2.5-Pro（本地模型）",
  mineru_local_service: "MinerU（本地部署服务）",
  paddleocr: "PaddleOCR（API）",
  paddleocr_local_service: "PaddleOCR-VL（本地部署服务）",
};

// 需要 endpoint_ref 的解析器（其余为纯本地解析器）。
const REMOTE_PARSERS = new Set(["mineru", "paddleocr", "mineru_local_service", "paddleocr_local_service"]);

export function ParserProfileTab({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<ParserProfileItem[]>([]);
  const [endpoints, setEndpoints] = useState<ParserEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<ParserProfileItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<{
    name: string;
    parser_name: string;
    endpoint_ref: string;
    functional: Record<string, string | boolean>;
  }>({ name: "", parser_name: "pymupdf4llm", endpoint_ref: "", functional: {} });

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get<{ items: ParserProfileItem[] }>(`/projects/${projectId}/parser-profiles?page=1&page_size=100`)
      .then((data) => setItems(data.items))
      .catch(() => toast.error("加载解析器配置失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  const fetchEndpoints = useCallback(() => {
    api
      .get<{ items: ParserEndpoint[] }>(`/projects/${projectId}/parser-profiles/endpoints`)
      .then((data) => setEndpoints(data.items))
      .catch(() => toast.error("加载服务端端点列表失败"));
  }, [projectId]);

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => {
      fetchItems();
      fetchEndpoints();
    }, 0);
    return () => window.clearTimeout(refreshTimer);
  }, [fetchItems, fetchEndpoints]);

  const handleParserChange = (parserName: string) => {
    const firstEndpoint = endpoints.find((e) => e.parser_name === parserName);
    setForm((prev) => ({
      ...prev,
      parser_name: parserName,
      endpoint_ref: firstEndpoint?.endpoint_ref || "",
      functional: {},
    }));
  };

  const openCreate = () => {
    setEditItem(null);
    setForm({
      name: "",
      parser_name: "pymupdf4llm",
      endpoint_ref: "",
      functional: {},
    });
    setDialogOpen(true);
  };

  const openEdit = (item: ParserProfileItem) => {
    setEditItem(item);
    const options = item.parser_options || {};
    const functional: Record<string, string | boolean> = {};
    const fields = FUNCTIONAL_FIELDS[item.parser_name] || [];
    for (const field of fields) {
      const value = options[field.key];
      if (value !== undefined) functional[field.key] = value as string | boolean;
    }
    setForm({
      name: item.name,
      parser_name: item.parser_name,
      endpoint_ref: String(options.endpoint_ref || ""),
      functional,
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error("请输入配置名称");
      return;
    }
    const parserName = form.parser_name;
    if (REMOTE_PARSERS.has(parserName) && !form.endpoint_ref) {
      toast.error("请选择服务端端点");
      return;
    }
    setSaving(true);
    try {
      // 只提交功能参数 + endpoint_ref；绝不拼入网络字段。
      const parser_options: Record<string, string | boolean | number> = {};
      if (REMOTE_PARSERS.has(parserName)) {
        parser_options.endpoint_ref = form.endpoint_ref;
      }
      for (const field of FUNCTIONAL_FIELDS[parserName] || []) {
        const value = form.functional[field.key];
        if (value === undefined || value === "") continue;
        if (field.type === "number") {
          const num = Number(value);
          if (!Number.isNaN(num)) parser_options[field.key] = num;
        } else {
          parser_options[field.key] = value as string | boolean;
        }
      }

      const payload = {
        name: form.name,
        parser_name: parserName,
        parser_options: Object.keys(parser_options).length > 0 ? parser_options : null,
      };

      if (editItem) {
        await api.patch(`/projects/${projectId}/parser-profiles/${editItem.id}`, payload);
        toast.success("保存成功");
      } else {
        await api.post(`/projects/${projectId}/parser-profiles/`, payload);
        toast.success("创建成功");
      }
      setDialogOpen(false);
      fetchItems();
    } catch (err) {
      const message = err instanceof Error ? err.message : "保存失败";
      toast.error(message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (item: ParserProfileItem) => {
    if (!confirm(`确定要删除解析器配置「${item.name}」吗？`)) return;
    try {
      await api.delete(`/projects/${projectId}/parser-profiles/${item.id}`);
      toast.success("已删除");
      fetchItems();
    } catch {
      toast.error("删除失败");
    }
  };

  const renderFunctionalFields = () => {
    const fields = FUNCTIONAL_FIELDS[form.parser_name] || [];
    if (fields.length === 0) {
      return (
        <p className="text-xs text-muted-foreground">
          该解析器无需额外功能参数。
        </p>
      );
    }
    return fields.map((field) => {
      if (field.type === "boolean") {
        return (
          <label key={field.key} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.functional[field.key] === true || form.functional[field.key] === "true"}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, functional: { ...prev.functional, [field.key]: e.target.checked } }))
              }
            />
            {field.label}
          </label>
        );
      }
      const inputId = `parser-field-${field.key}`;
      if (field.type === "select") {
        return (
          <div key={field.key}>
            <label htmlFor={inputId} className="text-sm font-medium">{field.label}</label>
            <select
              id={inputId}
              className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
              value={String(form.functional[field.key] ?? "")}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, functional: { ...prev.functional, [field.key]: e.target.value } }))
              }
            >
              <option value="">默认</option>
              {(field.options || []).map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          </div>
        );
      }
      return (
        <div key={field.key}>
          <label htmlFor={inputId} className="text-sm font-medium">{field.label}</label>
          <Input
            id={inputId}
            type={field.type === "number" ? "number" : "text"}
            value={String(form.functional[field.key] ?? "")}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, functional: { ...prev.functional, [field.key]: e.target.value } }))
            }
          />
        </div>
      );
    });
  };

  const endpointOptions = endpoints.filter((e) => e.parser_name === form.parser_name);

  const columns: ColumnDef<ParserProfileItem>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <button className="text-primary hover:underline" onClick={() => openEdit(row)}>
          {row.name}
        </button>
      ),
    },
    {
      key: "parser_name",
      header: "解析器",
      render: (row) => PARSER_LABELS[row.parser_name] || row.parser_name,
    },
    {
      key: "is_default",
      header: "默认",
      render: (row) => (row.is_default ? "✓" : ""),
    },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <div className="flex gap-1">
          <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
            <PencilIcon className="size-3" /> 编辑
          </Button>
          <Button variant="ghost" size="xs" className="text-destructive hover:text-destructive" onClick={() => handleDelete(row)}>
            删除
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-end mb-4">
        <Button onClick={openCreate}>
          <PlusIcon className="size-4" />
          新建
        </Button>
      </div>
      {loading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin inline mr-2" />
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={items}
          total={items.length}
          page={1}
          pageSize={100}
          onPageChange={() => {}}
          rowKey={(r) => r.id}
        />
      )}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editItem ? "编辑解析器配置" : "新建解析器配置"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label htmlFor="parser-name" className="text-sm font-medium">名称</label>
              <Input
                id="parser-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例如：MinerU 生产"
              />
            </div>
            <div>
              <label htmlFor="parser-type" className="text-sm font-medium">解析器类型</label>
              <select
                id="parser-type"
                className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                value={form.parser_name}
                onChange={(e) => handleParserChange(e.target.value)}
              >
                {Object.entries(PARSER_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            {REMOTE_PARSERS.has(form.parser_name) && (
              <div>
                <label htmlFor="parser-endpoint" className="text-sm font-medium">服务端端点</label>
                <select
                  id="parser-endpoint"
                  className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                  value={form.endpoint_ref}
                  onChange={(e) => setForm({ ...form, endpoint_ref: e.target.value })}
                >
                  <option value="">选择端点...</option>
                  {endpointOptions.map((ep) => (
                    <option key={ep.endpoint_ref} value={ep.endpoint_ref}>
                      {ep.display_name}
                      {ep.credential_configured ? "（凭证已配置）" : "（凭证未配置）"}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground mt-1">
                  端点由管理员管理；Token 由后端环境变量提供，不在本页展示或保存。
                </p>
              </div>
            )}
            {form.parser_name === "mineru_local" && (
              <p className="text-xs text-muted-foreground">
                加载本机 models 目录内的 MinerU 权重，首次解析需要较长模型加载时间。
              </p>
            )}
            {renderFunctionalFields()}
          </div>
          <DialogFooter>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2Icon className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
