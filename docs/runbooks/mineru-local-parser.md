# MinerU 本地解析功能使用指南

## 功能范围

系统现已支持在解析器配置中选择 `MinerU2.5-Pro（本地模型）`，使用仓库内已经准备好的
`models/MinerU2.5-Pro-2604-1.2B` 权重执行 PDF 到 Markdown 的解析。

运行链路如下：

```text
上传 PDF → 选择 MinerU2.5-Pro（本地模型） → 创建 ParseJob
→ PDF 按页渲染为图片 → MinerU 本地模型逐页推理
→ 合并 Markdown / 保存结构 JSON → 进入清洗流程
```

本地 MinerU 已作为标准解析能力进入后端环境。系统启动时会准备推理运行库，但不会读取
模型权重或占用模型推理内存；只有用户在文档页选择本地 MinerU 并发起任务后，API 才加载模型。

## 1. 使用本地 MinerU 启动系统

macOS / Linux / Git Bash：

```bash
cd /Users/liuyuze/Desktop/domain-dataset-gen
./scripts/dev-start.sh
```

Windows PowerShell：

```powershell
.\scripts\dev-start.ps1
```

普通一键启动现在会：

- 同步 MinerU 的 `transformers` 推理运行依赖；
- 使用具备本地解析能力的环境启动后端 API；
- 不会在系统启动时立刻加载 2.2 GB 模型，模型会在首次选用本地 MinerU 发起解析时加载。

旧命令 `./scripts/dev-start.sh --with-mineru` 仍可运行，但不再需要额外指定该参数。

## 2. 确认或创建 ParserProfile

新数据库会自动包含：

```text
MinerU2.5-Pro（本地模型）
```

已运行过种子数据的既有数据库，在再次执行含数据库初始化步骤的启动脚本后，也会自动为
现有项目补充缺失的本地 MinerU 配置。

也可以在网页中手动配置：

1. 进入项目的“设置”页面。
2. 打开“解析器”页签。
3. 新建解析器，类型选择“MinerU2.5-Pro（本地模型）”。
4. 保持或调整本地参数后保存。

默认参数：

| 参数 | 默认值 | 含义 |
|---|---|---|
| 本地模型目录 | `models/MinerU2.5-Pro-2604-1.2B` | 模型权重所在路径 |
| 推理设备 | `自动` | 交由 transformers 判断当前设备 |
| 页面 DPI | `160` | PDF 页面转图片的清晰度 |
| 分析图片与图表 | 关闭 | 初次正式解析先降低额外计算量 |

## 3. 发起本地解析

1. 进入项目的文档列表并上传 PDF，或打开已有 PDF。
2. 在文档详情页的解析器下拉框中选择 `MinerU2.5-Pro（本地模型）`。
3. 点击“发起解析”。
4. 等待 ParseJob 状态变为完成。
5. 从该 ParseJob 启动清洗工作台，检查 Markdown 内容。

首次 MinerU 解析会加载模型，通常明显慢于后续在同一个 API 进程中的解析任务。
本机运行默认串行处理 MinerU 推理，以避免多个重模型任务同时占用内存。

## 4. 解析结果存储变化

解析产物现按 ParseJob 独立保存：

```text
outputs/<project_id>/<document_id>/parsed/<parse_job_id>/raw.md
outputs/<project_id>/<document_id>/parsed/<parse_job_id>/structured.json
```

因此，同一 PDF 可以先后用 `pymupdf4llm` 与 `mineru_local` 解析，两次结果不会互相覆盖。
选择某个 ParseJob 发起清洗时，清洗流程将读取该任务对应的 Markdown。

## 5. 常见问题

### 选择本地 MinerU 后提示依赖未安装

说明当前 API 仍来自升级前启动的旧环境，或依赖尚未完成同步。停止并重新执行普通一键启动：

```bash
./scripts/dev-stop.sh
./scripts/dev-start.sh
```

### 提示模型不存在或不完整

确认下列文件存在：

```text
models/MinerU2.5-Pro-2604-1.2B/model.safetensors
```

若 ParserProfile 中修改过模型目录，需保证路径指向包含该文件的目录。

### 第一次解析较慢

这是正常现象。第一次任务需要将本地模型加载到 API 进程中；只要 API 进程未重启，
后续任务可以复用已加载的模型。

### 希望比较默认解析与 MinerU 的效果

对同一个 PDF 分别创建两次解析任务：

1. 选择 `PyMuPDF4LLM（本地）` 发起解析。
2. 选择 `MinerU2.5-Pro（本地模型）` 发起解析。
3. 在 ParseJob 记录中分别选择结果进入清洗流程，比较 Markdown。

### PyMuPDF4LLM 解析中文后出现问号或替换字符

`pymupdf4llm` 优先读取 PDF 内部已有的文字层，而不是对页面重新做 OCR。部分中文 PDF
使用子集字体或缺失/错误的 `ToUnicode` 字符映射，页面看上去正常，但内部字符编码不能
可靠地还原为 Unicode 文本，提取结果就可能出现大量 `�` 或问号。

这种情况不是 Markdown 渲染造成的，也不是简单切换字体能够修复。对该类文档，应选择
`MinerU2.5-Pro（本地模型）` 按页面图像重新识别；后续也可将 PaddleOCR 作为 OCR 兜底方案。
