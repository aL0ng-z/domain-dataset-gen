# domain-dataset-gen

压气机（compressor）领域知识抽取与数据集生产平台。项目目标是把 PDF 教材、手册、论文加工为可追溯、可审核、可复现的知识资产，并进一步组织成微调数据集与评测 benchmark。

当前仓库来自 macOS 环境迁移，已进入 R1/R1+ 测试加固阶段。前半段链路（上传、解析、清洗、分块）已重点打磨；LLM 生成、人工审核提升、数据集/评测集导出仍是 R1 最终验收重点。

## 1. 你应该先看什么

如果只是想把项目跑起来，直接看：

- [2. 推荐启动方式](#2-推荐启动方式)
- [3. 日常启动与关闭](#3-日常启动与关闭)
- [4. 页面使用流程](#4-页面使用流程)

如果要继续开发，再看：

- [7. 项目结构](#7-项目结构)
- [8. 常用开发命令](#8-常用开发命令)
- [9. 关键文档](#9-关键文档)

## 2. 推荐启动方式

仓库里确实已经有一键启动脚本：

```text
scripts/dev-start-conda.ps1  Windows + conda 一键启动（推荐）
scripts/dev-start.ps1        Windows + uv/.venv 一键启动
scripts/dev-stop.ps1         Windows 一键关闭
scripts/dev-start.sh         macOS / Linux / Git Bash 一键启动
scripts/dev-stop.sh          macOS / Linux / Git Bash 一键关闭
```

Windows 上建议优先使用 `dev-start-conda.ps1`。它假定你已经在 `DatasetGen` conda 环境中运行脚本，不创建 `.venv`，也不会替你切换 conda 环境。

简单选择：

- Windows + conda，想直接开始测试：用 [2.1 Windows + conda 一键脚本](#21-windows--conda-一键脚本推荐)。
- 允许项目创建 `.venv`，想沿用旧脚本：用 [2.2 uv/.venv 一键脚本](#22-uvvenv-一键脚本保留)。
- 想逐步排查或完全手动：用 [2.3 conda-only 手动启动](#23-conda-only-手动启动)。

### 2.1 Windows + conda 一键脚本（推荐）

这个脚本是为当前 Windows + conda 工作流新增的。它会检查当前 PowerShell 是否已经处于 `DatasetGen` 环境，然后启动基础设施、迁移数据库、初始化默认数据，并在后台启动 API 和 Web。脚本不安装 Python 或 npm 依赖，默认你已经准备好当前 conda 环境和前端依赖。

先确认本机已安装 Docker Desktop、Conda、Node.js/npm，并激活 `DatasetGen`：

```powershell
docker --version
conda --version
node --version
npm --version
conda activate DatasetGen
python --version
```

一键启动：

```powershell
.\scripts\dev-start-conda.ps1
```

这个脚本不支持任何启动参数。

脚本不安装依赖。如果提示前端依赖缺失，先手动执行一次：

```powershell
cd apps\web
npm ci
cd ..\..
```

首次遇到 PowerShell 执行策略限制时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev-start-conda.ps1
```

脚本默认要求当前 conda 环境名为 `DatasetGen`，Python 版本为 `3.11`。如果你没有先激活环境，脚本会直接提示 `conda activate DatasetGen` 并退出。

脚本会自动完成：

1. 创建 `infra/docker/.env`
2. 创建 `apps/api/.env`
3. 创建 `apps/web/.env.local`
4. 启动 PostgreSQL、Redis、MinIO
5. 检查当前 shell 是否为 `DatasetGen` / Python 3.11
6. 执行 Alembic 数据库迁移
7. 执行种子脚本，创建默认管理员和默认项目
8. 在后台启动 FastAPI 后端和 Next.js 前端

启动完成后访问：

```text
前端 Web: http://localhost:3000
后端健康检查: http://localhost:8000/api/health
API 文档: http://localhost:8000/docs
MinIO 控制台: http://localhost:9001
默认登录: admin / admin123
```

如果 `localhost:3000` 被占用，脚本会自动尝试 `3001-3005`。

API 和 Web 会在后台 PowerShell 进程中运行，日志写入：

```text
logs/R1plus-API.log
logs/R1plus-Web.log
```

脚本不会打开新的可见终端窗口；如果后端或前端启动失败，优先看上面的日志文件。

关闭：

```powershell
.\scripts\dev-stop.ps1       # 停止 API / Web，保留 Docker 基础设施
.\scripts\dev-stop.ps1 -All  # 同时停止 Docker 容器，容器仍会保留在 Docker Desktop
```

### 2.2 uv/.venv 一键脚本（保留）

原有一键脚本仍然可用，但它使用 `uv sync` 管理 Python 依赖，并会创建仓库内 `.venv`。如果你决定统一使用 conda，优先使用上一节的 `dev-start-conda.ps1`。

Windows PowerShell：

```powershell
.\scripts\dev-start.ps1
```

macOS / Linux / Git Bash：

```bash
./scripts/dev-start.sh
```

这套脚本会自动完成：

1. 创建本地 `.env`
2. 启动 PostgreSQL、Redis、MinIO
3. 使用 uv 安装/同步后端依赖
4. 安装/检查前端依赖
5. 执行 Alembic 数据库迁移
6. 执行种子脚本，创建默认管理员和默认项目
7. 启动 FastAPI 后端和 Next.js 前端

启动完成后访问：

```text
前端 Web: http://localhost:3000
后端健康检查: http://localhost:8000/api/health
API 文档: http://localhost:8000/docs
MinIO 控制台: http://localhost:9001
默认登录: admin / admin123
```

如果 `localhost:3000` 被占用，脚本会自动尝试 `3001-3005`。

关闭：

```powershell
.\scripts\dev-stop.ps1       # 停止 API / Web，保留 Docker 基础设施
.\scripts\dev-stop.ps1 -All  # 同时停止 Docker 容器，容器仍会保留在 Docker Desktop
```

### 2.3 conda-only 手动启动

如果新脚本启动失败，或你想逐步排查每一步，可以按下面流程手动启动。它同样不依赖 `.venv`。

#### 2.3.1 前置软件

确认已安装并可用：

- Git
- Docker Desktop，并确保 Docker 服务已经启动
- Conda
- Node.js 20 或 22
- npm

检查命令：

```powershell
git --version
docker --version
conda --version
node --version
npm --version
```

项目后端要求 Python `>=3.11,<3.12`。如果当前系统 Python 是 3.12/3.13，不要直接用系统 Python，创建 conda 环境：

```powershell
conda create -n DatasetGen python=3.11 -y
conda activate DatasetGen
python --version
```

#### 2.3.2 准备环境变量

在仓库根目录执行：

```powershell
Copy-Item infra/docker/.env.example infra/docker/.env -ErrorAction SilentlyContinue
Copy-Item apps/api/.env.example apps/api/.env -ErrorAction SilentlyContinue
```

创建前端本地环境变量：

```powershell
@"
NEXT_PUBLIC_API_URL=http://localhost:8000/api
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
"@ | Set-Content -LiteralPath apps/web/.env.local -Encoding UTF8
```

这些 `.env` 文件是本机运行配置，不提交 Git。

#### 2.3.3 启动基础设施

启动 PostgreSQL、Redis、MinIO：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d
```

查看状态：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps
```

期望 `postgres`、`redis`、`minio` 都处于 running/healthy。MinIO 控制台默认地址：

```text
http://localhost:9001
minioadmin / minioadmin123
```

#### 2.3.4 安装后端依赖到 conda 环境

保持 `conda activate DatasetGen`，在仓库根目录执行：

```powershell
python -m pip install -U pip setuptools wheel
python -m pip install -e libs/domain -e libs/storage -e libs/parsing -e libs/cleaning -e libs/splitters -e libs/llm -e "apps/api[dev]"
```

说明：

- `libs/*` 是后端内部库，需要以 editable 模式装入同一个 conda 环境。
- `apps/api[dev]` 安装 FastAPI 后端和测试/ruff 等开发依赖。
- `libs/parsing` 会安装本地 MinerU 解析所需依赖，首次安装可能较慢。

#### 2.3.5 迁移数据库并初始化默认数据

```powershell
cd apps/api
alembic upgrade head
python ../../scripts/init_seed.py
cd ../..
```

种子脚本会创建：

- 默认管理员：`admin / admin123`
- 默认项目：`压气机知识抽取`
- 默认解析器、分块配置、导出配置、任务策略
- 默认 Prompt 模板

脚本是幂等的。重复执行时提示已初始化是正常现象。

#### 2.3.6 启动后端 API

打开终端 A：

```powershell
conda activate DatasetGen
cd C:\Work\Postdoc\Test\03_LLM\domain-dataset-gen\apps\api
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

验证：

```text
http://localhost:8000/api/health
http://localhost:8000/docs
```

健康检查应返回：

```json
{"status":"ok"}
```

#### 2.3.7 启动前端 Web

打开终端 B：

```powershell
cd C:\Work\Postdoc\Test\03_LLM\domain-dataset-gen\apps\web
npm ci
npm run dev
```

访问：

```text
http://localhost:3000
```

如果 3000 被占用，可以手动换端口：

```powershell
npm run dev -- -p 3001
```

## 3. 日常启动与关闭

如果使用 Windows + conda 一键脚本，日常只需要：

```powershell
.\scripts\dev-start-conda.ps1
.\scripts\dev-stop.ps1
```

如果使用 uv/.venv 一键脚本，则运行 `.\scripts\dev-start.ps1`。如果使用 conda-only 手动流程，日常按下面三步。

### 3.1 启动 Docker 基础设施

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d
```

### 3.2 启动后端

```powershell
conda activate DatasetGen
cd C:\Work\Postdoc\Test\03_LLM\domain-dataset-gen\apps\api
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3.3 启动前端

```powershell
cd C:\Work\Postdoc\Test\03_LLM\domain-dataset-gen\apps\web
npm run dev
```

### 3.4 关闭

后端和前端终端按 `Ctrl + C`。

关闭 Docker 基础设施：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
```

如果要删除本地数据库和 MinIO 数据卷，使用：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down -v
```

`down -v` 会清空数据库和对象存储，只在确定要重置开发数据时使用。

## 4. 页面使用流程

默认登录：

```text
用户名：admin
密码：admin123
```

推荐从默认项目 `压气机知识抽取` 开始试跑。

### 4.1 最小可跑链路

1. 登录后进入项目列表。
2. 打开默认项目。
3. 进入“文档”页，上传一个 PDF。
4. 打开文档详情页。
5. 选择解析器并点击“发起解析”。
6. ParseJob 完成后，选择对应解析任务进入清洗。
7. 在清洗工作台逐页检查/编辑 Markdown，提交并审核章节。
8. 合并清洗版本，执行最终审核。
9. 回到文档详情页执行“分块”。
10. 分块完成后进入 chunk 列表或文档详情页执行生成。
11. 到“候选”页审核 LLM 生成结果。
12. 将合格 Candidate 提升为 CuratedItem。
13. 在“数据集”或“评测集”页加入 CuratedItem 并导出。

### 4.2 解析器怎么选

| 解析器 | 适合场景 | 额外要求 |
|---|---|---|
| `PyMuPDF4LLM（本地）` | PDF 自带文字层质量较好；最快，适合先跑通流程 | 无 |
| `MinerU2.5-Pro（本地模型）` | 中文 PDF 文字层乱码、公式/版面更复杂 | 本机 `models/MinerU2.5-Pro-2604-1.2B/model.safetensors` 存在 |
| `MinerU（API）` | 使用 MinerU 官方/远程 API | 在 `apps/api/.env` 配置 `MINERU_API_TOKEN` |
| `PaddleOCR（API）` | 使用 PaddleOCR 远程文档解析 API | 在 `apps/api/.env` 配置 `PADDLEOCR_API_TOKEN` |
| `MinerU（本地部署服务 / MLX）` | macOS/MLX 本地服务链路 | 主要面向原 Mac 环境；Windows 下不建议作为第一选择 |
| `PaddleOCR-VL（本地部署服务 / MLX）` | macOS/MLX 本地服务链路 | 主要面向原 Mac 环境；Windows 下不建议作为第一选择 |

新手建议先用 `PyMuPDF4LLM（本地）` 跑通。如果中文抽取结果出现大量问号或替换字符，再改用 `MinerU2.5-Pro（本地模型）`。

`models/` 是本地大模型权重目录，已经被 `.gitignore` 忽略，不会进入 Git。新机器 clone 仓库后如果没有模型文件，本地 MinerU 不能用，但默认 PyMuPDF 仍可用。

### 4.3 LLM 生成前必须配置模型

解析、清洗、分块不需要外部 LLM。生成 Candidate 需要 OpenAI-compatible 模型网关。

在页面中进入：

```text
项目 → 设置 → 模型配置
```

填写：

- Provider：例如 `deepseek`、`openai`、`openrouter`、`local`
- API 地址：例如 `https://api.deepseek.com/v1` 或本地 `http://localhost:8080/v1`
- API Key
- 模型名称：例如 `deepseek-chat` 或你的本地服务模型名

然后可以使用默认 Prompt 模板执行单个 chunk 或整篇文档的生成任务。

## 5. 当前项目状态

| 模块 | 状态 |
|---|---|
| 登录/用户/项目 | R1 可用 |
| 配置中心 | ModelConfig、ParserProfile、ChunkProfile、ExportProfile、TaskPolicy 已接入 |
| PDF 上传 | R1 可用，文件进入 MinIO |
| 解析 | PyMuPDF、本地 MinerU、远程 MinerU/PaddleOCR、本地服务型解析器已接入 |
| 清洗 | 支持按 ParseJob 选择来源、按页 Section、编辑/审核/合并/终审 |
| 分块 | 支持 section-aware chunking |
| LLM 生成 | 后端链路和 UI 已接入，依赖模型配置和 prompt |
| 候选审核/提升 | Candidate 到 CuratedItem 链路已接入 |
| 数据集/评测集/导出 | R1 骨架已接入，仍需继续验收 |
| 监控/任务 | 任务列表、WebSocket 状态推送、用量统计基础能力已接入 |

## 6. 核心架构

主流程：

```text
Upload PDF
  → Parse via ParserProfile
  → Clean/Verify by Section
  → Chunk
  → LLM Generate Candidate
  → Human Review
  → CuratedItem
  → Dataset / Benchmark Export
```

关键对象：

```text
Document → Section → Chunk → Candidate → CuratedItem → Dataset / Benchmark
```

关键设计约束：

- Section 是清洗协作单位。
- Chunk 是 LLM 生成单位。
- Candidate 是 LLM 草稿，不是正式资产。
- CuratedItem 是人工审核后的正式知识资产，也是导出的唯一可信来源。
- ParseJob 独立保存解析结果，同一 PDF 可以用不同解析器多次解析并比较。
- 导出应冻结上游版本，保证可复现。

## 7. 项目结构

```text
apps/web/                 Next.js 16 + TypeScript + Tailwind + shadcn/ui
apps/api/                 FastAPI + SQLAlchemy + Alembic
apps/api/app/workers/     解析、清洗、分块、生成、导出等后台任务
apps/api/migrations/      Alembic 数据库迁移
libs/domain/              通用 schema / DTO
libs/storage/             MinIO / S3 封装
libs/parsing/             PyMuPDF4LLM、MinerU、PaddleOCR 解析器封装
libs/cleaning/            Section 切分与清洗辅助逻辑
libs/splitters/           chunk 切分策略
libs/llm/                 OpenAI-compatible LLM client
infra/docker/             PostgreSQL、Redis、MinIO docker compose
scripts/                  启停脚本、种子数据、本地解析服务 helper
tests/                    后端单元/服务测试
docs/                     PRD、工程计划、runbook、开发日志
models/                   本地模型权重；不提交 Git
logs/                     本地运行日志；不提交 Git
```

## 8. 常用开发命令

### 8.1 后端测试

在已激活的 conda 环境中，从仓库根目录执行：

```powershell
python -m pytest -q
```

### 8.2 后端 lint

```powershell
python -m ruff check .
```

### 8.3 前端类型检查

```powershell
cd apps/web
npm exec tsc -- --noEmit
```

### 8.4 前端 lint

```powershell
cd apps/web
npm run lint
```

### 8.5 数据库迁移

```powershell
conda activate DatasetGen
cd apps/api
alembic upgrade head
```

新增迁移示例：

```powershell
cd apps/api
alembic revision --autogenerate -m "describe change"
```

## 9. 关键文档

- 产品需求：[docs/product/PRD.md](docs/product/PRD.md)
- 工程计划：[docs/plans/compressor-knowledge-platform-engineering-plan.md](docs/plans/compressor-knowledge-platform-engineering-plan.md)
- 开发日志：[docs/logs/dev-log.md](docs/logs/dev-log.md)
- R1 测试指南：[docs/r1-testing-guide.md](docs/r1-testing-guide.md)
- R1 问题记录：[docs/r1-testing-issues.md](docs/r1-testing-issues.md)
- 新机器部署 runbook：[docs/runbooks/new-machine-runbook.md](docs/runbooks/new-machine-runbook.md)
- MinerU 本地解析指南：[docs/runbooks/mineru-local-parser.md](docs/runbooks/mineru-local-parser.md)
- MinerU 本地服务指南：[docs/runbooks/mineru-local-service.md](docs/runbooks/mineru-local-service.md)
- PaddleOCR 本地服务指南：[docs/runbooks/paddleocr-local-service.md](docs/runbooks/paddleocr-local-service.md)
- 文档目录说明：[docs/README.md](docs/README.md)

## 10. 常见问题

### 10.1 后端启动时报 Python 版本不匹配

项目要求 Python 3.11。确认：

```powershell
conda activate DatasetGen
python --version
```

如果不是 3.11，重新创建 conda 环境。

### 10.2 前端请求后端失败

检查：

- 后端是否运行在 `http://localhost:8000`
- `apps/web/.env.local` 中 `NEXT_PUBLIC_API_URL` 是否为 `http://localhost:8000/api`
- 浏览器控制台是否有 CORS 或 401 错误

### 10.3 数据库连接失败

先确认 Docker 服务：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps
```

再确认 `apps/api/.env` 中：

```text
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

本机 FastAPI 连接 Docker 映射端口时，host 应该是 `localhost`；容器内部互联才使用 `postgres`。

### 10.4 登录失败或没有默认项目

重新执行种子脚本：

```powershell
conda activate DatasetGen
cd apps/api
python ../../scripts/init_seed.py
```

### 10.5 PyMuPDF 解析中文乱码

部分中文 PDF 的内部文字层损坏或缺失 ToUnicode 映射，页面看起来正常但文本抽取会变成问号或替换字符。这种情况优先换 `MinerU2.5-Pro（本地模型）`，让模型按页面图像重新识别。

### 10.6 本地 MinerU 提示模型不存在

确认文件存在：

```text
models/MinerU2.5-Pro-2604-1.2B/model.safetensors
```

如果没有模型权重，可以先用 `PyMuPDF4LLM（本地）` 跑通主流程。

### 10.7 生成 Candidate 失败

常见原因：

- 没有在项目设置里创建 ModelConfig。
- API Key 无效。
- `base_url` 没有指向 OpenAI-compatible `/v1` 服务。
- 模型不支持 JSON object response format。
- chunk 状态还不是可生成状态。

先用“项目 → 设置 → 模型配置”里的测试按钮验证模型配置。
