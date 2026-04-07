"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { api } from "@/lib/api";
import { PlusIcon, PencilIcon, Loader2Icon, ZapIcon } from "lucide-react";

/* ========= Shared types ========= */
interface ConfigItem {
  id: string;
  name: string;
  [key: string]: unknown;
}

/* ========= ModelConfig tab ========= */

const PROVIDER_PRESETS: Record<string, { base_url: string; models: string[] }> = {
  openai: {
    base_url: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  },
  deepseek: {
    base_url: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  siliconflow: {
    base_url: "https://api.siliconflow.cn/v1",
    models: ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "Pro/deepseek-ai/DeepSeek-R1"],
  },
  openrouter: {
    base_url: "https://openrouter.ai/api/v1",
    models: ["openai/gpt-4o", "anthropic/claude-sonnet-4", "deepseek/deepseek-chat-v3-0324"],
  },
  vllm: {
    base_url: "http://localhost:8080/v1",
    models: [],
  },
  other: {
    base_url: "",
    models: [],
  },
};

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  deepseek: "DeepSeek",
  siliconflow: "硅基流动 (SiliconFlow)",
  openrouter: "OpenRouter",
  vllm: "vLLM (本地)",
  other: "其他",
};

interface ModelConfig extends ConfigItem {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_encrypted?: string;
  max_tokens?: number;
  temperature?: number;
}

function ModelConfigTab({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<ModelConfig | null>(null);
  const [form, setForm] = useState({
    name: "",
    provider: "deepseek",
    model_name: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    api_key: "",
    max_tokens: 2048,
    temperature: 0.7,
  });
  const [testing, setTesting] = useState(false);

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get<{ items: ModelConfig[] }>(
        `/projects/${projectId}/model-configs?page=1&page_size=100`
      )
      .then((data) => setItems(data.items))
      .catch(() => toast.error("加载模型配置失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const handleProviderChange = (provider: string) => {
    const preset = PROVIDER_PRESETS[provider];
    setForm({
      ...form,
      provider,
      base_url: preset?.base_url || "",
      model_name: preset?.models[0] || "",
    });
  };

  const openCreate = () => {
    setEditItem(null);
    const defaultProvider = "deepseek";
    const preset = PROVIDER_PRESETS[defaultProvider];
    setForm({
      name: "",
      provider: defaultProvider,
      model_name: preset.models[0] || "",
      base_url: preset.base_url,
      api_key: "",
      max_tokens: 2048,
      temperature: 0.7,
    });
    setDialogOpen(true);
  };

  const openEdit = (item: ModelConfig) => {
    setEditItem(item);
    setForm({
      name: item.name,
      provider: item.provider,
      model_name: item.model_name,
      base_url: item.base_url || "",
      api_key: "", // Don't show existing key, leave blank to keep unchanged
      max_tokens: item.max_tokens ?? 2048,
      temperature: item.temperature ?? 0.7,
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error("请输入配置名称");
      return;
    }
    if (!form.base_url.trim()) {
      toast.error("请输入 API 地址");
      return;
    }
    if (!editItem && !form.api_key.trim()) {
      toast.error("请输入 API Key");
      return;
    }
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        provider: form.provider,
        model_name: form.model_name,
        base_url: form.base_url,
        max_tokens: form.max_tokens,
        temperature: form.temperature,
      };
      // Only send api_key if provided (for edit, empty means keep existing)
      if (form.api_key.trim()) {
        payload.api_key = form.api_key;
      }
      if (editItem) {
        await api.patch(
          `/projects/${projectId}/model-configs/${editItem.id}`,
          payload
        );
        toast.success("保存成功");
        setDialogOpen(false);
      } else {
        const created = await api.post<ModelConfig>(
          `/projects/${projectId}/model-configs/`,
          payload
        );
        toast.success("创建成功，可点击「测试连接」验证");
        setEditItem(created); // Switch to edit mode so test button appears
      }
      fetchItems();
    } catch {
      toast.error("保存失败");
    }
  };

  const handleTestConnection = async () => {
    if (!editItem) {
      toast.error("请先保存配置后再测试连接");
      return;
    }
    setTesting(true);
    try {
      const res = await api.post<{ status: string; response?: string; error?: string }>(
        `/projects/${projectId}/model-configs/${editItem.id}/test`,
        {}
      );
      if (res.status === "success") {
        toast.success(`连接成功: ${res.response}`);
      } else {
        toast.error(`连接失败: ${res.error}`);
      }
    } catch {
      toast.error("连接测试失败");
    } finally {
      setTesting(false);
    }
  };

  const columns: ColumnDef<ModelConfig>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <button
          className="text-primary hover:underline"
          onClick={() => openEdit(row)}
        >
          {row.name}
        </button>
      ),
    },
    { key: "provider", header: "提供商", render: (row) => PROVIDER_LABELS[row.provider] || row.provider },
    { key: "model_name", header: "模型", render: (row) => row.model_name },
    { key: "base_url", header: "API 地址", render: (row) => <span className="text-xs text-muted-foreground truncate max-w-48 block">{row.base_url}</span> },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
          <PencilIcon className="size-3" />
          编辑
        </Button>
      ),
    },
  ];

  const currentPreset = PROVIDER_PRESETS[form.provider];

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
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editItem ? "编辑模型配置" : "新建模型配置"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">名称</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例如：DeepSeek 生产"
              />
            </div>
            <div>
              <label className="text-sm font-medium">提供商</label>
              <select
                className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                value={form.provider}
                onChange={(e) => handleProviderChange(e.target.value)}
              >
                {Object.entries(PROVIDER_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">API 地址</label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.example.com/v1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">API Key</label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder={editItem ? "留空则保持原 Key 不变" : "请输入 API Key"}
              />
            </div>
            <div>
              <label className="text-sm font-medium">模型名称</label>
              {currentPreset?.models.length ? (
                <select
                  className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                  value={form.model_name}
                  onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                >
                  {currentPreset.models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  <option value="__custom">自定义...</option>
                </select>
              ) : (
                <Input
                  value={form.model_name}
                  onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                  placeholder="请输入模型名称"
                />
              )}
              {form.model_name === "__custom" && (
                <Input
                  className="mt-1"
                  value=""
                  onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                  placeholder="请输入自定义模型名称"
                  autoFocus
                />
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium">最大 Token</label>
                <Input
                  type="number"
                  value={form.max_tokens}
                  onChange={(e) =>
                    setForm({ ...form, max_tokens: Number(e.target.value) })
                  }
                />
              </div>
              <div>
                <label className="text-sm font-medium">温度</label>
                <Input
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={form.temperature}
                  onChange={(e) =>
                    setForm({ ...form, temperature: Number(e.target.value) })
                  }
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            {editItem && (
              <Button
                variant="outline"
                onClick={handleTestConnection}
                disabled={testing}
              >
                {testing ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <ZapIcon className="size-4" />
                )}
                测试连接
              </Button>
            )}
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ========= Generic Config Tab ========= */
interface GenericConfig extends ConfigItem {
  description?: string;
  config_json?: string;
}

function GenericConfigTab({
  projectId,
  endpoint,
  label,
  extraFields,
}: {
  projectId: string;
  endpoint: string;
  label: string;
  extraFields?: { key: string; label: string; type?: string }[];
}) {
  const [items, setItems] = useState<GenericConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<GenericConfig | null>(null);
  const [form, setForm] = useState<Record<string, string>>({
    name: "",
    description: "",
    config_json: "{}",
  });

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get<{ items: GenericConfig[] }>(
        `/projects/${projectId}/${endpoint}?page=1&page_size=100`
      )
      .then((data) => setItems(data.items))
      .catch(() => toast.error(`加载${label}失败`))
      .finally(() => setLoading(false));
  }, [projectId, endpoint, label]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const openCreate = () => {
    setEditItem(null);
    const defaults: Record<string, string> = {
      name: "",
      description: "",
      config_json: "{}",
    };
    extraFields?.forEach((f) => {
      defaults[f.key] = "";
    });
    setForm(defaults);
    setDialogOpen(true);
  };

  const openEdit = (item: GenericConfig) => {
    setEditItem(item);
    const vals: Record<string, string> = {
      name: item.name,
      description: item.description || "",
      config_json:
        typeof item.config_json === "string"
          ? item.config_json
          : JSON.stringify(item.config_json || {}, null, 2),
    };
    extraFields?.forEach((f) => {
      vals[f.key] = String((item as Record<string, unknown>)[f.key] || "");
    });
    setForm(vals);
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name?.trim()) {
      toast.error("请输入名称");
      return;
    }
    try {
      // Parse config_json to send as object
      let payload: Record<string, unknown> = { ...form };
      try {
        payload.config_json = JSON.parse(form.config_json || "{}");
      } catch {
        // keep as string if invalid json
      }
      if (editItem) {
        await api.patch(
          `/projects/${projectId}/${endpoint}/${editItem.id}`,
          payload
        );
      } else {
        await api.post(
          `/projects/${projectId}/${endpoint}/`,
          payload
        );
      }
      toast.success("保存成功");
      setDialogOpen(false);
      fetchItems();
    } catch {
      toast.error("保存失败");
    }
  };

  const columns: ColumnDef<GenericConfig>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <button
          className="text-primary hover:underline"
          onClick={() => openEdit(row)}
        >
          {row.name}
        </button>
      ),
    },
    {
      key: "description",
      header: "描述",
      render: (row) => row.description || "-",
    },
    ...(extraFields?.map((f) => ({
      key: f.key,
      header: f.label,
      render: (row: GenericConfig) =>
        String((row as Record<string, unknown>)[f.key] || "-"),
    })) || []),
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
          <PencilIcon className="size-3" />
          编辑
        </Button>
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
            <DialogTitle>
              {editItem ? `编辑${label}` : `新建${label}`}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">名称</label>
              <Input
                value={form.name || ""}
                onChange={(e) =>
                  setForm({ ...form, name: e.target.value })
                }
              />
            </div>
            <div>
              <label className="text-sm font-medium">描述</label>
              <Input
                value={form.description || ""}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
              />
            </div>
            {extraFields?.map((f) => (
              <div key={f.key}>
                <label className="text-sm font-medium">{f.label}</label>
                <Input
                  type={f.type || "text"}
                  value={form[f.key] || ""}
                  onChange={(e) =>
                    setForm({ ...form, [f.key]: e.target.value })
                  }
                />
              </div>
            ))}
            <div>
              <label className="text-sm font-medium">配置 (JSON)</label>
              <Textarea
                value={form.config_json || "{}"}
                onChange={(e) =>
                  setForm({ ...form, config_json: e.target.value })
                }
                className="min-h-24 font-mono text-xs"
              />
            </div>
          </div>
          <DialogFooter>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ========= Main Settings Page ========= */
export default function SettingsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">项目设置</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理各类配置文件
        </p>
      </div>

      <Tabs defaultValue="model">
        <TabsList>
          <TabsTrigger value="model">模型配置</TabsTrigger>
          <TabsTrigger value="parser">解析器</TabsTrigger>
          <TabsTrigger value="chunk">切分</TabsTrigger>
          <TabsTrigger value="export">导出</TabsTrigger>
          <TabsTrigger value="task">任务策略</TabsTrigger>
        </TabsList>

        <TabsContent value="model">
          <Card>
            <CardHeader>
              <CardTitle>模型配置 (ModelConfig)</CardTitle>
            </CardHeader>
            <CardContent>
              <ModelConfigTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="parser">
          <Card>
            <CardHeader>
              <CardTitle>解析器配置 (ParserProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                endpoint="parser-profiles"
                label="解析器配置"
                extraFields={[
                  { key: "parser_type", label: "解析器类型" },
                ]}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="chunk">
          <Card>
            <CardHeader>
              <CardTitle>切分配置 (ChunkProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                endpoint="chunk-profiles"
                label="切分配置"
                extraFields={[
                  {
                    key: "max_tokens",
                    label: "最大Token数",
                    type: "number",
                  },
                  { key: "strategy", label: "切分策略" },
                ]}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="export">
          <Card>
            <CardHeader>
              <CardTitle>导出配置 (ExportProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                endpoint="export-profiles"
                label="导出配置"
                extraFields={[
                  { key: "format", label: "导出格式" },
                ]}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="task">
          <Card>
            <CardHeader>
              <CardTitle>任务策略 (TaskPolicy)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                endpoint="task-policies"
                label="任务策略"
                extraFields={[
                  {
                    key: "max_concurrency",
                    label: "最大并发",
                    type: "number",
                  },
                  {
                    key: "retry_limit",
                    label: "重试次数",
                    type: "number",
                  },
                ]}
              />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
