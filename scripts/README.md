# 运行脚本

本目录保留项目启停、初始化及后端实际调用的本地解析服务脚本。日常运行使用 Windows PowerShell 和 conda `DatasetGen` 环境。

| 脚本 | 运行用途 |
|---|---|
| `dev-start-conda.ps1` | 检查当前环境与依赖；Docker 引擎未就绪时自动启动 Docker Desktop，再启动 PostgreSQL、Redis、MinIO，执行数据库迁移和初始化，后台启动 API、worker 与 Web。 |
| `dev-stop.ps1` | 根据 PID 文件停止 API、worker 与 Web；加 `-All` 同时停止本项目 Docker 容器。 |
| `init_seed.py` | 创建默认账号、项目、解析配置、切分配置、导出配置、任务策略和提示模板，由启动脚本调用。 |
| `mineru_local_service.py` | 准备、配置和启动本地 MinerU 服务；后端选择本地服务型解析器时调用。 |
| `paddleocr_local_service.py` | 准备、配置和启动 PaddleOCR-VL 服务；后端选择对应服务型解析器时调用。其 MLX 推理服务需要 Apple Silicon 环境。 |

## 启动与关闭

在仓库根目录执行：

```powershell
conda activate DatasetGen
.\scripts\dev-start-conda.ps1
```

启动脚本要求 Python 3.11，使用已安装的 Python 与前端依赖。Web 默认地址为 `http://localhost:3000`，API 健康检查为 `http://localhost:8000/api/health`。

Docker 引擎已运行时直接复用；未就绪时执行 `docker desktop start --timeout 120`，等待启动完成，再用 `docker info` 确认引擎可用。需要安装支持该命令的 Docker Desktop，并完成首次初始化及所选后端的系统设置；项目容器使用 Linux 容器模式。脚本不会安装 Docker Desktop 或修改系统虚拟化设置。

新机器首次运行时，Compose 会拉取缺失的镜像，并按 `infra/docker/docker-compose.yml` 创建容器、网络、端口映射以及 PostgreSQL/MinIO 数据卷；随后初始化数据库和存储桶。已有环境配置和数据卷会复用。首次拉取镜像需要网络可访问镜像仓库。

```powershell
.\scripts\dev-stop.ps1
.\scripts\dev-stop.ps1 -All
```

运行日志及 PID 文件位于 `logs/`。API、worker 和 Web 的标准输出、错误输出统一保存为 UTF-8，启动完成后在当前终端实时显示，分别带有 `[API]`、`[Worker]`、`[Web]` 前缀。按 `Ctrl+C` 只退出日志查看，后台服务继续运行；用 `dev-stop.ps1` 停止服务。

再次启动时，上一轮日志会保留为 `R1plus-*.log.<时间戳>.bak`，当前 `.log` 文件重新以 UTF-8 写入。旧的混合编码文件仅备份，不自动转换。若只查看单个文件，可使用 `Get-Content -LiteralPath .\logs\R1plus-API.log -Encoding UTF8 -Tail 50 -Wait`；Docker 容器日志通过 `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env logs -f postgres redis minio` 查看。

两个本地解析服务脚本保留是因为后端仍有运行时调用；Windows 默认可使用 PyMuPDF 或本地 MinerU 模型解析。

独立 Bash 启动、测试、审计、旧数据迁移、重置和接口生成工具已移除。数据库结构迁移仍由 `apps/api/migrations/` 中的 Alembic 迁移维护，一键启动会自动执行 `alembic upgrade head`。
