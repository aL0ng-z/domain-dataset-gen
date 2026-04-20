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

## 3. 配置 Docker 环境变量

复制环境变量模板：

```powershell
Copy-Item infra/docker/.env.example infra/docker/.env
```

默认开发配置通常可直接使用，无需修改。

## 4. 启动基础依赖服务

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

## 5. 安装后端依赖（apps/api）

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

## 6. 执行数据库迁移

在 `apps/api` 目录执行：

```powershell
uv run alembic upgrade head
```

## 7. 初始化种子数据

在 `apps/api` 目录执行：

```powershell
uv run python ../../scripts/init_seed.py
```

此步骤会创建：

- 默认管理员账号：`admin / admin123`
- 默认项目
- 默认配置（解析器、切分、导出、任务策略）
- 默认 Prompt 模板

## 8. 启动后端 API（终端 A）

在 `apps/api` 目录执行：

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：浏览器访问 `http://localhost:8000/api/health`，应返回 `{"status":"ok"}`。

## 9. 启动前端（终端 B）

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

## 10. 登录验证

在网页登录页使用：

- 用户名：`admin`
- 密码：`admin123`

可以进入项目列表并打开默认项目，说明前后端链路正常。

## 11. MinIO 验证（可选）

访问 MinIO 控制台：`http://localhost:9001`

默认账号：

- Access Key: `minioadmin`
- Secret Key: `minioadmin123`

应能看到桶：

- `documents`
- `outputs`

## 12. 停止服务

- 停止前后端开发服务：在对应终端按 `Ctrl + C`
- 停止 Docker 服务：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down
```

## 13. 常见问题排查

### 13.1 端口冲突

如果 `5432/6379/9000/9001/8000/3000` 被占用：

- 关闭占用程序，或
- 修改 `infra/docker/.env`（基础服务端口）
- 后端改 `uvicorn --port`，前端改 `npm run dev -- -p <端口>`

### 13.2 数据库迁移失败

请先确认 PostgreSQL 已正常启动：

```powershell
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps
```

然后重试：

```powershell
cd apps/api
uv run alembic upgrade head
```

### 13.3 前端无法请求后端

优先检查：

- 后端是否正在监听 `http://localhost:8000`
- 前端环境变量 `NEXT_PUBLIC_API_URL` 是否为 `http://localhost:8000/api`

当前代码默认会回退到上述地址。

### 13.4 种子脚本提示已初始化

这是正常行为（幂等检查），表示管理员和默认数据已存在。

---

如需“一键启动/一键停止”脚本，可在此基础上新增 `scripts/start-dev.ps1` 与 `scripts/stop-dev.ps1`。
