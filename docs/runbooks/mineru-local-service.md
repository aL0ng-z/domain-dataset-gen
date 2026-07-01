# MinerU 本地部署服务验证指南

## 目标与范围

本指南覆盖本地 MinerU 服务化验证及其平台接入方式：

```text
仓库内已下载的 MinerU2.5-Pro 权重
→ 独立的官方 mineru-api 服务
→ 上传一份 PDF 并获得 Markdown
```

独立服务验证已通过，平台现已增加 `mineru_local_service` 解析器并与现有 `mineru_local + transformers` 并存。本文前半部分保留独立冒烟步骤，后半部分说明平台中的使用入口。

最低通过标准：

- 服务使用 `models/MinerU2.5-Pro-2604-1.2B` 中的本地权重；
- PDF 不发送到 MinerU 官方远程 API；
- 不需要配置 `MINERU_API_TOKEN`；
- `mineru-api` 能返回可读 Markdown；
- 记录当前运行设备上实际可用的推理后端，为后续 GPU + vLLM 部署提供基线。

## 为什么服务环境独立

平台当前使用 Python 3.11，并在平台进程内通过 Transformers 加载模型。当前开发机为 Apple Silicon macOS，而 vLLM 在 macOS 上属于实验支持，`vllm-metal` 又要求原生 arm64 Python 3.12。

因此第一阶段使用独立目录：

```text
.venv/                       # 现有平台，不改变
.venv-mineru-service/        # 独立 MinerU 服务环境
logs/mineru-local-service/   # 服务配置与冒烟结果
models/MinerU2.5-Pro-2604-1.2B/  # 共享的现有权重目录
```

这样，当前 Mac 的服务验证和未来 Linux + NVIDIA GPU 的 vLLM 部署都不会把重推理依赖强行混入平台运行环境。

## 1. 生成本地模型配置

在仓库根目录执行：

```bash
./.venv/bin/python scripts/mineru_local_service.py config
```

脚本验证 `model.safetensors` 是否存在，并生成：

```text
logs/mineru-local-service/mineru.json
```

配置中的 `models-dir.vlm` 直接指向仓库现有 MinerU 模型目录；运行服务时脚本同时设置：

```bash
MINERU_MODEL_SOURCE=local
MINERU_TOOLS_CONFIG_JSON=<生成的 mineru.json>
```

这表示 MinerU 仅从本地路径读取 VLM 权重。

## 2. 创建独立 MinerU 服务环境

此步骤会安装官方 MinerU 服务依赖，并可能下载 Python 3.12 与 Python 包，但不会下载模型权重：

```bash
./.venv/bin/python scripts/mineru_local_service.py setup
```

默认安装官方 `mineru[all]>=3.0.0`，原因是最新 `mineru-api` 已负责 PDF 接收、异步任务与文档解析编排。这里不复用平台中只面向页面图片推理的 `mineru-vl-utils[transformers]`。

## 3. 在当前 Mac 上启动服务

首先用官方自动 VLM 后端验证本地服务能否读取权重并完成解析：

```bash
./.venv/bin/python scripts/mineru_local_service.py serve --port 9010
```

另开终端检查健康接口：

```bash
./.venv/bin/python scripts/mineru_local_service.py health
```

然后提交自动生成的一页 PDF：

```bash
./.venv/bin/python scripts/mineru_local_service.py smoke \
  --backend vlm-auto-engine
```

也可以使用真实 PDF：

```bash
./.venv/bin/python scripts/mineru_local_service.py smoke \
  --backend vlm-auto-engine \
  --pdf "/path/to/sample.pdf"
```

需验证图片或图表解析时，可以额外添加 `--image-analysis`；首轮链路验证默认关闭该能力以缩短无关推理开销。

成功产物写入：

```text
logs/mineru-local-service/smoke/<时间戳>/
├── generated-smoke.pdf     # 未传 --pdf 时生成
├── health.json
├── response.json
└── result.md
```

说明：当前 Mac 验证的目标首先是“独立本地服务 + 本地权重 + 无官方 Token”链路。`vlm-auto-engine` 在当前硬件实际选择的内部加速路径必须以运行结果为准，不将其预先表述为 vLLM。

## 本次 Mac 验证结果（2026-05-26）

本轮已在当前 Apple Silicon Mac 上实际执行：

```text
独立 Python 3.12 环境
→ 安装 mineru 3.1.15
→ 启动本地 mineru-api (127.0.0.1:9010)
→ backend=vlm-auto-engine 提交一页 PDF
→ 返回 Markdown: MinerU local service PDF smoke test
```

验证结果：

| 项目 | 结果 |
|---|---|
| 本地模型配置 | `models-dir.vlm` 指向仓库 `models/MinerU2.5-Pro-2604-1.2B` |
| 官方 API Token | 未配置、未使用 |
| 服务健康接口 | `/health` 返回 `version=3.1.15`、`status=healthy` |
| PDF 转 Markdown | 成功 |
| Mac 实际推理引擎 | 服务日志明确记录 `Using mlx-engine as the inference engine for VLM.` |
| vLLM 状态 | 当前服务环境未安装 `vllm`；本次不构成 vLLM 推理验证 |

官方 `mineru 3.1.15` 的自动引擎选择逻辑会在受支持的 macOS Apple Silicon 设备上选择 `mlx-engine`，而 Linux 环境在已安装 `vllm` 时可自动选择 vLLM。因此，这次结果证明的是本地服务协议与本地权重路径可行；GPU 迁移时仍需单独完成 vLLM 性能与稳定性验收。

## 4. vLLM 路线的验收方式

### 当前 Apple Silicon Mac

vLLM 官方对 macOS Apple Silicon 的原生支持仍标为实验性；GPU 加速需要社区维护的 `vllm-metal`，且要求原生 arm64 Python 3.12。因此在 Mac 上，vLLM 属于专项可行性验证，不作为第一阶段服务链路通过的前置条件。

当本地 vLLM 兼容服务成功启动后，可让 MinerU 通过 HTTP client 使用它：

```bash
./.venv/bin/python scripts/mineru_local_service.py smoke \
  --backend vlm-http-client \
  --server-url "http://127.0.0.1:30000"
```

该命令的通过标准是：`mineru-api` 负责编排 PDF，而页面级 VLM 推理由指定的 OpenAI-compatible vLLM 服务完成。

### 未来 Linux + NVIDIA GPU

正式部署建议采用：

```text
平台 FastAPI
→ mineru-api（PDF 解析编排）
→ vLLM 模型服务（加载自有 MinerU 权重）
```

GPU 主机上先启动官方支持的 vLLM/OpenAI-compatible 模型服务，再启动 `mineru-api`，并使用 `backend=vlm-http-client` 与该 `server_url` 做同样的冒烟验证。平台适配器只依赖 `mineru-api` 的协议，因此未来替换推理硬件时不需要重写平台业务流程。

## 5. 与平台现有实现的关系

| 解析方式 | 状态 | 模型执行位置 | 官方 Token |
|---|---|---|---|
| `mineru` | 已有 | MinerU 官方远程 API | 需要 |
| `mineru_local` | 已有 | 平台 API 进程内 Transformers | 不需要 |
| `mineru_local_service` | 已接入 | 解析任务按需拉起独立 `mineru-api`，当前 Mac 为 MLX，未来 GPU 可切换到 vLLM | 不需要 |

## 6. 在平台中使用本地部署服务

本地服务环境仅需准备一次：

```bash
./.venv/bin/python scripts/mineru_local_service.py setup
```

之后按普通方式启动平台即可：

```bash
./scripts/dev-start.sh
```

无需额外指定本地服务启动参数。选择 `MinerU（本地部署服务 / MLX）` 发起解析任务后，后端会先探测 `http://127.0.0.1:9010/health`；服务尚未运行时自动启动本地 `mineru-api`，随后继续当前 ParseJob。服务默认不预加载模型，第一次解析任务会读取本地权重。

自动拉起只应用于服务地址为 `http://127.0.0.1:<port>` 或 `http://localhost:<port>` 的配置。将 ParserProfile 指向 GPU 主机等外部地址时，平台只连接该地址，不会在 Mac 上误启一个替代服务。此前的 `--with-mineru-service` 参数仍可输入以兼容旧操作记录，但已不再决定服务是否可用。

平台中的使用步骤：

1. 在项目设置的解析器配置中确认存在 `MinerU（本地部署服务 / MLX）`。旧数据库在再次运行启动脚本的种子初始化步骤后会自动补齐该配置。
2. 在文档详情页发起解析时选择该解析器。
3. 当前 Mac 默认以 `vlm-auto-engine` 请求按需启动的本地服务，官方服务内部选择 MLX。
4. 解析成功后，结果仍按 ParseJob 保存，可从对应解析记录进入清洗流程。

未来迁移到 GPU 主机时，将该 ParserProfile 的“服务推理路径”改为“外部 VLM 服务（未来 GPU / vLLM）”，并填写 vLLM 服务地址；平台侧 ParseJob 与清洗流程不需要改动。

## 参考资料

- [MinerU 官方基础使用文档](https://opendatalab.github.io/MinerU/zh/usage/quick_usage/)
- [MinerU 官方模型源配置](https://opendatalab.github.io/MinerU/zh/usage/model_source/)
- [MinerU 官方命令行与 API 说明](https://opendatalab.github.io/MinerU/usage/cli_tools/)
- [MinerU 官方 vLLM 参数说明](https://opendatalab.github.io/MinerU/usage/advanced_cli_parameters/)
- [vLLM Apple Silicon 支持说明](https://docs.vllm.ai/en/latest/getting_started/installation/cpu/?device=arm)
- [vLLM-Metal 安装说明](https://docs.vllm.ai/projects/vllm-metal/en/latest/installation/)
