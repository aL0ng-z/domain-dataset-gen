# Code Review 报告与机制复现材料

基线：`aL0ng-z/domain-dataset-gen@019f9ea70fb2325aad068e03cc7637372a9fe273`。

主报告是 `domain-dataset-gen-code-review-2026-09-14.md`。共 22 项发现：8 项 P1、14 项 P2；2 项有真实 CI 记录，15 项有源码与本地机制级验证，5 项仅完成源码 / 合同核对。

## 重要边界

这些脚本是从已读取代码中人工提取 / 规范化的错误机制及隔离替身，**没有导入完整项目代码，不是项目原测试，不是浏览器 E2E，也不等价于 PostgreSQL / MinIO 集成测试**。

脚本中的断言以“观察到缺陷现象”为成功，因此 exit code 0 并不表示项目健康。修改仓库源文件不会自动使这些独立脚本转绿；应把对应场景迁移到仓库测试，导入真实实现后再验证修复。

所有运行只访问本机临时 HTTP / WebSocket 服务和临时文件 / SQLite 数据库。不访问线上服务，不使用真实账号或 API key。

## 文件

| 文件 | 内容 |
|---|---|
| 主报告 .md | 来源、问题清单、详细根因、修复及回归建议 |
| reproduce_backend.py | 11 个 Python / SQLAlchemy / ASGI 机制用例 |
| reproduce_frontend.mjs | 5 个 Node / fetch / Promise 机制用例 |
| backend-results.json / frontend-results.json | 本次实际执行的结构化结果 |
| backend-output.txt / frontend-output.txt | 本次脚本的控制台输出 |
| ci-evidence.md | 已有真实 CI 日志的关键摘录与链接 |
| findings.json | 可供后续整理 Issue 的结构化审查结果 |

## 运行

在独立 Python 环境中安装 `sqlalchemy`、`fastapi`、`uvicorn`、`websockets`，并准备 Node 22：

```bash
python reproduce_backend.py
node reproduce_frontend.mjs
```

本次实际环境：Python 3.13.5、SQLAlchemy 2.0.50、FastAPI 0.128.2、Starlette 0.50.0、Uvicorn 0.48.0、websockets 16.0、Node v22.16.0。它不是项目的 Python 3.11 锁定环境。确切版本记录也在结果 JSON 中。

无需原仓库或外部服务即可观察这些机制；真正修复验收仍需项目的干净构建、存量数据库迁移、双会话竞争、真实 worker / Redis / MinIO 及浏览器回归。
