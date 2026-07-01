# PaddleOCR-VL 本地 API 冒烟验证指南

## 目标与阶段边界

本指南对应 PaddleOCR 本地部署服务。独立服务链路已通过验证，并已作为平台 `paddleocr_local_service` 解析方式接入：

```text
models/PaddleOCR-VL-1.5-0.9B 原始本地权重
-> logs/paddleocr-local-service/models/PaddleOCR-VL-1.5-MLX 转换产物
-> MLX-VLM 内部推理服务 (127.0.0.1:9021)
-> PaddleX 完整文档解析 API (127.0.0.1:9020/layout-parsing)
-> PDF 转 Markdown
```

该拆分遵循 PaddleOCR 官方 Apple Silicon 方案：`mlx_vlm.server` 负责 VLM 推理，`paddlex --serve --pipeline ...` 负责 PDF 页面处理、版面分析和 `/layout-parsing` API 协议。

最低通过标准：

- 原始下载权重在本机转换为 MLX 格式，VLM 请求中的模型名指向转换后的本地路径；
- PaddleX 对外提供 `/layout-parsing`，响应格式与现有远程 PaddleOCR parser 兼容；
- 提交 PDF 能返回可读 Markdown；
- 不配置、不发送 PaddleOCR 官方远程 API token；
- 版面检测使用仓库内 `models/PP-DocLayoutV3`，避免启动完整 pipeline 时隐式访问远程模型源。

## 隔离与端口

本轮不复用 MinerU 服务环境，也不占用平台常见端口：

| 用途 | 位置或端口 | 说明 |
|---|---|---|
| 平台 Python 环境 | `.venv/` | 不改动 |
| MinerU 本地 API | `9010` | 既有实现，不改动 |
| PaddleOCR 完整 API 环境 | `.venv-paddleocr-service/` | PaddlePaddle / PaddleX Serving，Python 3.11 |
| PaddleOCR MLX 环境 | `.venv-paddleocr-mlx-service/` | MLX-VLM 内部推理，Python 3.12 |
| 版面检测模型 | `models/PP-DocLayoutV3/` | PaddleX 读取的本地 `PP-DocLayoutV3` 推理模型 |
| MLX 模型产物 | `logs/paddleocr-local-service/models/PaddleOCR-VL-1.5-MLX/` | 由原始本地权重离线转换，不提交仓库 |
| PaddleOCR 完整 API | `9020` | 避免官方示例 `8080` 与未来 vLLM/LLM 配置冲突 |
| PaddleOCR 内部 MLX-VLM | `9021` | 避免官方示例 `8111` 和 MinerU 端口冲突 |
| 运行产物/缓存 | `logs/paddleocr-local-service/` | 配置、模型缓存和冒烟输出 |

启动脚本会为两个服务进程补入 `NO_PROXY/no_proxy=127.0.0.1,localhost,::1`，确保 PaddleX 到内部 MLX 服务的本机请求不受开发机代理设置影响。

## 为什么不是只启动 MLX-VLM

仓库下载的 `PaddleOCR-VL-1.5-0.9B` 是文档 VLM 组件。Apple Silicon 上的 `mlx_vlm.server` 读取 MLX 格式权重，因此冒烟工具先在本地离线转换一份服务产物；这不是远程下载模型。完整 PDF 解析还需要 PaddleOCR-VL pipeline 中的版面检测模块，当前官方模板使用 `PP-DocLayoutV3`。因此平台未来应调用 PaddleX 的 `/layout-parsing`，而不是直接调用只处理 VLM 请求的 MLX 服务。

完整 API 启动时会从 `models/PP-DocLayoutV3` 读取版面检测模型；若该目录缺少 `inference.json`、`inference.pdiparams` 或 `inference.yml`，脚本会直接失败并提示先下载，而不是让 PaddleX 隐式访问远程模型源。

若当前机器暂时无法取得 `PP-DocLayoutV3`，脚本还提供显式的 `--whole-page-smoke` 模式：PaddleX 仍提供同一 `/layout-parsing` API，但关闭布局检测并把完整页面作为 `ocr` 区块发送给本地 VLM。该模式用于先验收服务协议、本地权重与 Markdown 输出，不等价于完成复杂版面的正式质量验收。

## 1. 准备独立服务环境

在仓库根目录执行：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py setup
```

脚本将创建两个互相独立的环境并安装：

- `.venv-paddleocr-service/`：`paddlepaddle>=3.2.1`、`paddleocr[doc-parser]>=3.5.0`、`paddlex[serving]>=3.5.0`
- `.venv-paddleocr-mlx-service/`：`mlx-vlm==0.3.10`、`mlx-lm==0.30.5`、`mlx==0.31.1`、`mlx-metal==0.31.1`、`transformers==5.0.0rc3`，以及原模型处理器导入所需的 `torch==2.12.0` / `torchvision==0.27.0`

安装完成后，先将已下载权重转换为 MLX 本地格式：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py convert
```

再下载完整布局检测所需的 `PP-DocLayoutV3`：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py download-layout
```

默认保存到：

```text
models/PP-DocLayoutV3/
├── inference.json
├── inference.pdiparams
└── inference.yml
```

转换只读取 `models/PaddleOCR-VL-1.5-0.9B`，产物保存在运行目录内；启动完整 API 时会从官方 `PaddleOCR-VL-1.5` 模板生成：

```text
logs/paddleocr-local-service/config/PaddleOCR-VL-1.5.yaml
```

生成配置会将 `LayoutDetection.model_dir` 指向本地 `models/PP-DocLayoutV3`，并将 `VLRecognition.genai_config.backend` 改为 `mlx-vlm-server`。服务 URL 指向 `http://127.0.0.1:9021`，请求模型名指向本机转换后的 MLX 权重绝对路径。这里必须使用服务根地址：PaddleX 的 OpenAI 客户端会自行追加聊天补全路由，写成 `/v1` 会请求到不存在的路径。

说明：既有 MinerU 使用的 `mlx-vlm==0.3.9` 不包含 `paddleocr_vl` 实现，实际请求会由内部服务返回 `502`。本阶段已验证 `mlx-vlm==0.3.10` 的安装包包含 `mlx_vlm.models.paddleocr_vl`，因此 PaddleOCR 使用单独的 MLX 环境和版本约束，不改动 MinerU 环境。

## 2. 启动内部 MLX-VLM 服务

在第一个终端执行：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py vlm-serve
```

此命令为 MLX 进程设置 Hugging Face / Transformers 离线模型模式，并启用 `MLX_TRUST_REMOTE_CODE=true` 以加载转换产物中随本地模型保存的 PaddleOCR-VL 处理器实现；真正收到推理请求时，服务必须读取请求中提供的本地模型路径，而不能下载远程 VLM 权重。

## 3. 启动完整 PaddleX API

在第二个终端执行：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py api-serve
```

该进程通过结构化修改后的官方 pipeline 配置启动：

```text
http://127.0.0.1:9020/layout-parsing
```

完整 API 默认以 `cpu` 运行本地 `PP-DocLayoutV3` 布局检测部分，将 VLM 推理委派给 `9021` 上的 MLX-VLM。

仅在布局模型尚未准备好而需要先做链路冒烟时，改为：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py api-serve --whole-page-smoke
```

## 4. 执行 PDF 转 Markdown 冒烟

服务就绪后，在第三个终端执行：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py health
./.venv/bin/python scripts/paddleocr_local_service.py smoke
```

若完整 API 使用 `--whole-page-smoke` 启动，则提交请求也应带相同参数：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py smoke --whole-page-smoke
```

`smoke` 默认自动生成包含 `PaddleOCR local API smoke test` 文本的一页 PDF，也可指定真实 PDF：

```bash
./.venv/bin/python scripts/paddleocr_local_service.py smoke \
  --pdf "/path/to/sample.pdf"
```

成功产物写入：

```text
logs/paddleocr-local-service/smoke/<时间戳>/
├── generated-smoke.pdf
├── response.json
└── result.md
```

## 本机冒烟记录（2026-05-27）

- 已在 Apple Silicon Mac 上创建两个隔离环境，MLX 环境版本为 `mlx-vlm==0.3.10`、`mlx-lm==0.30.5`、`transformers==5.0.0rc3`、`torch==2.12.0` 与 `torchvision==0.27.0`，依赖一致性检查通过。
- `convert` 已将仓库原始权重离线转换为约 `1.7G` 的 `logs/paddleocr-local-service/models/PaddleOCR-VL-1.5-MLX/model.safetensors`。
- 使用 `vlm-serve` 与 `api-serve --whole-page-smoke` 启动服务后，`health` 通过；`smoke --whole-page-smoke` 请求 `/layout-parsing` 返回 `200`，结果 Markdown 为 `PaddleOCR local API smoke test`，未配置官方 API token。
- 2026-05-28 已补齐本地 `PP-DocLayoutV3` 到 `models/PP-DocLayoutV3`，并执行不带 `--whole-page-smoke` 的完整布局检测链路冒烟。`/layout-parsing` 返回 `200`，`prunedResult` 包含 `layout_det_res`，Markdown 为 `PaddleOCR local API smoke test`。

## 平台接入方式

平台中已新增 `paddleocr_local_service`，与远程 `paddleocr` 并存：

| 解析方式 | 对外调用位置 | Token | 本阶段状态 |
|---|---|---|---|
| `paddleocr` | 官方/远程 `/layout-parsing` | 需要 | 已存在 |
| `paddleocr_local_service` | 自有 PaddleX `/layout-parsing` | 不需要 | 已接入 |

新数据库和既有项目执行种子初始化后，会补齐默认 ParserProfile：

```text
PaddleOCR-VL（本地部署服务 / MLX）
parser_name = paddleocr_local_service
base_url = http://127.0.0.1:9020/layout-parsing
vlm_base_url = http://127.0.0.1:9021
```

解析任务选择该 profile 时，后端会按需自动启动两个本地进程：

- `PaddleOCR-VLM.pid`：内部 MLX-VLM 推理服务，默认 `9021`
- `PaddleOCR-API.pid`：PaddleX `/layout-parsing` 完整解析 API，默认 `9020`

日志写入：

```text
logs/paddleocr-local-service/managed-vlm.log
logs/paddleocr-local-service/managed-api.log
```

`./scripts/dev-stop.sh` 会同时停止这两个按需启动的服务。未来迁入 GPU 后，仅把 VLM 内部推理层由 `mlx-vlm-server` 切换为 `vllm-server` 或 FastDeploy，平台调用的完整 API 协议保持不变。

## 官方资料

- [PaddleOCR-VL Pipeline 使用与 API 服务部署](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
- [PaddleOCR-VL Apple Silicon 推理指南](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html)
