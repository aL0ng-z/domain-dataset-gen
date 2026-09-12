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
import { DataTable, type ColumnDef } from "@/components/data-table";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
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
  max_tokens?: number | null;
  temperature?: number | null;
  extra_params?: { [key: string]: unknown } | null;
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
    max_tokens: "" as string | number,
    temperature: "" as string | number,
  });
  const [testing, setTesting] = useState(false);
  const [useCustomModel, setUseCustomModel] = useState(false);

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/model-configs/", {
        params: { pid: projectId },
        query: { page: 1, page_size: 100 },
      })
      .then((data) => setItems(data.items))
      .catch(() => toast.error("加载模型配置失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => {
      fetchItems();
    }, 0);
    return () => window.clearTimeout(refreshTimer);
  }, [fetchItems]);

  const handleProviderChange = (provider: string) => {
    const preset = PROVIDER_PRESETS[provider];
    setForm({
      ...form,
      provider,
      base_url: preset?.base_url || "",
      model_name: preset?.models[0] || "",
    });
    setUseCustomModel(false);
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
      max_tokens: "",
      temperature: "",
    });
    setUseCustomModel(false);
    setDialogOpen(true);
  };

  const openEdit = (item: ModelConfig) => {
    setEditItem(item);
    const preset = PROVIDER_PRESETS[item.provider];
    const isKnownModel = preset?.models.includes(item.model_name);
    setForm({
      name: item.name,
      provider: item.provider,
      model_name: item.model_name,
      base_url: item.base_url || "",
      api_key: "",
      max_tokens: item.max_tokens ?? "",
      temperature: item.temperature ?? "",
    });
    setUseCustomModel(!isKnownModel && !!preset?.models.length);
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
      const payload: components["schemas"]["ModelConfigCreate"] = {
        name: form.name,
        provider: form.provider,
        model_name: form.model_name,
        base_url: form.base_url,
        api_key: form.api_key,
      };
      if (form.api_key.trim()) {
        payload.api_key = form.api_key;
      }
      if (form.max_tokens !== "" && form.max_tokens !== null) {
        payload.max_tokens = Number(form.max_tokens);
      }
      if (form.temperature !== "" && form.temperature !== null) {
        payload.temperature = Number(form.temperature);
      }
      if (editItem) {
        await api.patch("/projects/{pid}/model-configs/{config_id}", payload, {
          params: { pid: projectId, config_id: editItem.id },
        });
        toast.success("保存成功");
        setDialogOpen(false);
      } else {
        const created = await api.post("/projects/{pid}/model-configs/", payload, {
          params: { pid: projectId },
        });
        toast.success("创建成功，可点击「测试连接」验证");
        setEditItem(created as ModelConfig); // Switch to edit mode so test button appears
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
      const res = await api.post(
        "/projects/{pid}/model-configs/{config_id}/test",
        {},
        { params: { pid: projectId, config_id: editItem.id } }
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
              {currentPreset?.models.length && !useCustomModel ? (
                <select
                  className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                  value={form.model_name}
                  onChange={(e) => {
                    if (e.target.value === "__custom") {
                      setUseCustomModel(true);
                      setForm({ ...form, model_name: "" });
                    } else {
                      setForm({ ...form, model_name: e.target.value });
                    }
                  }}
                >
                  {currentPreset.models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  <option value="__custom">自定义...</option>
                </select>
              ) : (
                <div className="flex gap-2">
                  <Input
                    value={form.model_name}
                    onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                    placeholder="请输入模型名称"
                    autoFocus={useCustomModel}
                  />
                  {currentPreset?.models.length ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setUseCustomModel(false);
                        setForm({ ...form, model_name: currentPreset.models[0] || "" });
                      }}
                    >
                      选择
                    </Button>
                  ) : null}
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium">最大 Token</label>
                <Input
                  type="number"
                  value={form.max_tokens}
                  onChange={(e) =>
                    setForm({ ...form, max_tokens: e.target.value === "" ? "" : Number(e.target.value) })
                  }
                  placeholder="默认由 API 决定"
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
                    setForm({ ...form, temperature: e.target.value === "" ? "" : Number(e.target.value) })
                  }
                  placeholder="默认由 API 决定"
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

/* ========= ParserProfile tab ========= */

// ParserProfile 安全合同（T03）：不再允许自由网络字段。
// 组件实现在 components/parser-profile-tab.tsx。
import { ParserProfileTab } from "@/components/parser-profile-tab";
import { ChunkProfileTab, ExportProfileTab, TaskPolicyTab } from "@/components/typed-config-tabs";

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
              <ParserProfileTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="chunk">
          <Card>
            <CardHeader>
              <CardTitle>切分配置 (ChunkProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <ChunkProfileTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="export">
          <Card>
            <CardHeader>
              <CardTitle>导出配置 (ExportProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <ExportProfileTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="task">
          <Card>
            <CardHeader>
              <CardTitle>任务策略 (TaskPolicy)</CardTitle>
            </CardHeader>
            <CardContent>
              <TaskPolicyTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
