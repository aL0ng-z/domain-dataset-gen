# 新电脑部署与运行指南（domain-dataset-gen）

本文档用于在一台全新电脑上，从 `git clone` 到本项目可运行的完整步骤。

## 1. 前置安装

请先安装以下软件（仅需一次）：

- Git
- Docker Desktop（并确保 Docker 服务已启动）
- Python 3.11
- Node.js 20 或 22（推荐 LTS）
- uv（推荐，用于 Python 依赖管理）

可选检查命令（PowerShell）：

```powershell
git --version
docker --version
python --version
node --version
npm --version
uv --version
```

## 2. 克隆项目

```powershell
git clone <你的仓库地址>
cd domain-dataset-gen
```

## 3. 推荐：一键启动

首次运行前请确认 Docker Desktop 已启动。脚本会自动完成：

1. 创建 `infra/docker/.env`
2. 创建 `apps/api/.env`，让本机 FastAPI 能连接 Docker 中的 PostgreSQL / Redis / MinIO
3. 创建 `apps/web/.env.local`
4. 启动 PostgreSQL、Redis、MinIO
5. 安装缺失的后端/前端依赖
6. 执行 Alembic 数据库迁移
7. 执行种子脚本，创建默认管理员、默认项目和基础配置
8. 启动 FastAPI 后端和 Next.js 前端

macOS / Linux / Git Bash：

```bash
./scripts/dev-start.sh
```

Windows PowerShell：

```powershell
.\scripts\dev-start.ps1
```

常用参数：

```bash
./scripts/dev-start.sh --infra-only   # 只启动 Docker 基础设施、迁移、种子数据
./scripts/dev-start.sh --no-web       # 不启动前端
./scripts/dev-start.sh --no-api       # 不启动后端
./scripts/dev-start.sh --skip-install # 跳过依赖安装检查
```

普通一键启动已经包含仓库 `models/` 目录中的 MinerU 本地模型解析能力。
启动时只安装/校验推理运行库；首次在网页中选择该解析器处理 PDF 时，
模型权重才会被加载到内存中。详细步骤见
[`mineru-local-parser.md`](./mineru-local-parser.md)。

启动成功后访问：

- 前端：`http://localhost:3000`
- 后端健康检查：`http://localhost:8000/api/health`
- API 文档：`http://localhost:8000/docs`
- MinIO 控制台：默认 `http://localhost:9001`

默认登录：

- 用户名：`admin`
- 密码：`admin123`

## 4. 一键关闭

仅停止 API / Web，保留 PostgreSQL、Redis、MinIO：

```bash
./scripts/dev-stop.sh
```

Windows PowerShell：

```powershell
.\scripts\dev-stop.ps1
```

停止 API / Web，并关闭 Docker 基础设施：

```bash
./scripts/dev-stop.sh --all
```

Windows PowerShell：

```powershell
.\scripts\dev-stop.ps1 -All
```

脚本关闭逻辑：

1. 读取 `logs/R1plus-API.pid` 和 `logs/R1plus-Web.pid`
2. 停止后端、前端进程树
3. 如果传入 `--all` / `-All`，执行 `docker compose down`

停止脚本只会关闭本项目启动脚本记录的进程，不会按端口强行停止其他项目。若 `localhost:3000` 被另一个 Docker 项目占用，本项目启动脚本会自动选择 `3001-3005` 中的可用端口。

---

以下是手动启动步骤，适合排查一键脚本无法完成的情况。

## 5. 配置 Docker 环境变量

复制环境变量模板：

```powershell
Copy-Item infra/docker/.env.example infra/docker/.env
```

默认开发配置通常可直接使用，无需修改。

## 6. 启动基础依赖服务

启动 PostgreSQL、Redis、MinIO：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d
```

查看服务状态：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps
```

期望状态：

- `postgres` 为 running/healthy
- `redis` 为 running/healthy
- `minio` 为 running/healthy
- `minio-init` 执行完成（退出成功）

## 7. 安装后端依赖（apps/api）

进入后端目录：

```powershell
cd apps/api
```

如果未安装 `uv`，先安装：

```powershell
pip install uv
```

安装后端依赖：

```powershell
uv sync --extra dev
```

## 8. 执行数据库迁移

在 `apps/api` 目录执行：

```powershell
uv run alembic upgrade head
```

## 9. 初始化种子数据

在 `apps/api` 目录执行：

```powershell
uv run python ../../scripts/init_seed.py
```

此步骤会创建：

- 默认管理员账号：`admin / admin123`
- 默认项目
- 默认配置（解析器、切分、导出、任务策略）
- 默认 Prompt 模板

## 10. 启动后端 API（终端 A）

在 `apps/api` 目录执行：

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：浏览器访问 `http://localhost:8000/api/health`，应返回 `{"status":"ok"}`。

## 11. 启动前端（终端 B）

打开新终端，进入前端目录：

```powershell
cd apps/web
```

安装依赖（推荐使用 lock 文件）：

```powershell
npm ci
```

启动开发服务：

```powershell
npm run dev
```

浏览器访问：`http://localhost:3000`

## 12. 登录验证

在网页登录页使用：

- 用户名：`admin`
- 密码：`admin123`

可以进入项目列表并打开默认项目，说明前后端链路正常。

## 13. MinIO 验证（可选）

访问 MinIO 控制台：`http://localhost:9001`

默认账号：

- Access Key: `minioadmin`
- Secret Key: `minioadmin123`

应能看到桶：

- `documents`
- `outputs`

## 14. 停止服务

- 停止前后端开发服务：在对应终端按 `Ctrl + C`
- 停止 Docker 服务：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
```

## 15. 常见问题排查

### 15.1 端口冲突

如果 `5432/6379/9000/9001/8000/3000` 被占用：

- 关闭占用程序，或
- 修改 `infra/docker/.env`（基础服务端口）
- 后端改 `uvicorn --port`，前端改 `npm run dev -- -p <端口>`

### 15.2 数据库迁移失败

请先确认 PostgreSQL 已正常启动：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps
```

然后重试：

```powershell
cd apps/api
uv run alembic upgrade head
```

### 15.3 前端无法请求后端

优先检查：

- 后端是否正在监听 `http://localhost:8000`
- 前端环境变量 `NEXT_PUBLIC_API_URL` 是否为 `http://localhost:8000/api`

当前代码默认会回退到上述地址。

### 15.4 种子脚本提示已初始化

这是正常行为（幂等检查），表示管理员和默认数据已存在。

---

一键脚本已经内置在 `scripts/dev-start.*` 与 `scripts/dev-stop.*`。
