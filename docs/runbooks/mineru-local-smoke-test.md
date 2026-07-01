# MinerU 本地模型冒烟测试

## 目的

本测试用于在正式接入 `ParserProfile` 和 ParseJob 流程前，确认本机能否使用已下载的
`models/MinerU2.5-Pro-2604-1.2B` 模型将少量 PDF 页面转换为 Markdown。

测试脚本不会修改数据库、MinIO 或网页端流程；所有产物默认写入被忽略的
`logs/mineru-local-tests/<时间戳>/` 目录。

## 前置条件

- 项目后端虚拟环境已存在，并包含当前基础依赖：`./scripts/dev-start.sh` 曾成功运行即可。
- 本地模型目录存在：

```text
models/MinerU2.5-Pro-2604-1.2B/model.safetensors
```

- 准备一份用于测试的 PDF。首次测试建议使用 1 至 3 页、包含可核对文字或表格的样本。

## 1. 只验证 PDF 渲染

这一模式不加载 MinerU 模型。它验证 PDF 能否被读取、选择页码并渲染成模型需要的页面图片。

在仓库根目录执行：

```bash
./.venv/bin/python scripts/test_mineru_local.py \
  --pdf "/path/to/sample.pdf" \
  --pages 1 \
  --render-only
```

预期输出：

```text
logs/mineru-local-tests/<时间戳>/
├── pages/page-0001.png
└── run-report.json
```

## 2. 执行本地 MinerU 推理

正式接入后，普通一键启动已经同步官方推荐的 `transformers` 推理依赖，可直接使用项目环境执行：

```bash
cd apps/api
uv run --python 3.11 \
  python ../../scripts/test_mineru_local.py \
  --pdf "/path/to/sample.pdf" \
  --pages 1
```

脚本只读取本地模型目录，不会从网络下载模型权重。首次执行需要较长的模型加载时间。

预期输出：

```text
logs/mineru-local-tests/<时间戳>/
├── pages/page-0001.png
├── page-0001.json
├── page-0001.md
├── result.json
├── result.md
└── run-report.json
```

## 3. 扩大测试范围

单页成功后，可测试连续三页：

```bash
cd apps/api
uv run --python 3.11 \
  python ../../scripts/test_mineru_local.py \
  --pdf "/path/to/sample.pdf" \
  --pages "1-3"
```

页码格式支持：

| 参数 | 含义 |
|---|---|
| `--pages 1` | 仅测试第 1 页，默认值 |
| `--pages "1-3,5"` | 测试第 1 至 3 页以及第 5 页 |
| `--pages all` | 测试全部页面，不建议在初次验证时使用 |
| `--dpi 160` | 页面渲染清晰度，默认 160 |
| `--image-analysis` | 启用图片或图表分析，首次测试建议关闭 |
| `--device-map cpu` | 若自动设备分配失败，可尝试 CPU 推理 |
| `--debug` | 失败时将完整异常同时打印到终端 |

## 结果判断

测试通过的最低标准：

- `result.md` 中能看到页面主要文字内容；
- 表格或公式页面的结构输出可阅读；
- `run-report.json` 状态为 `completed`；
- 页级推理耗时在本机可以接受。

若模型加载失败、内存不足或单页耗时不可接受，应先记录报告，不直接接入系统正式解析流程。
