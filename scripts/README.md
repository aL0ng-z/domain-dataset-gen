# 脚本目录说明

运行 Python 脚本前先执行 `conda activate DatasetGen`。本目录只保留可重复使用的开发、测试、审计和运维入口；一次性实验脚本不得长期留存。

| 类别 | 脚本 | 生命周期与用途 |
|---|---|---|
| 开发服务 | `dev-start*`、`dev-stop*` | 本地开发长期入口；启动/停止 API、Web、worker 与依赖服务。stop 只处理脚本生成的 PID 文件。 |
| 自动化门禁 | `test-backend*`、`test-frontend*`、`test-infra*` | 本地与 CI 的长期入口；后端门禁会重建并迁移专用 `datasetgen_test.public` schema，只允许连接专用测试 PostgreSQL、Redis、MinIO。 |
| 原生命令回归 | `test-native-command.ps1` | 验证共享 `native-command.ps1` 对 stderr 警告、真实非零退出及调用环境恢复的处理；不连接数据库或其他服务。 |
| 迁移验证 | `run-migration-smoke*` | 长期入口；会重建并 upgrade/downgrade 专用测试数据库 schema，禁止指向生产库。 |
| 合同 | `export_openapi.py` | 长期入口；默认更新 OpenAPI 快照，`--check` 只比较且不写入。 |
| 只读审计 | `data-ownership-audit.py`、`clean_version_audit.py`、`composition_audit.py` | 发布前或迁移前执行；只读检查孤儿、跨项目引用、重复版本和 hash 一致性。 |
| ParserProfile 迁移 | `migrate_parser_profiles.py` | 受控迁移入口；优先使用 `--dry-run` 和 `--check`，无参数会在单事务中写数据库。 |
| 导出 orphan | `export_orphan_cleanup.py` | 长期运维入口；默认 dry-run，默认保留 24 小时。只有同时给出 `--apply --confirm-bucket <精确桶名>` 才删除未被 seal 引用、且不属于活跃 Export 的精确对象版本。 |
| 本地解析器 | `mineru_local_service.py`、`paddleocr_local_service.py`、`test_mineru_local.py` | 本地模型安装、配置、服务和冒烟验证；模型与验证结果属于本机资产，不随仓库清理。 |
| 初始化 | `init_seed.py` | 开发环境种子数据写入；不得对生产环境随意执行。 |

破坏性参数必须先在 dry-run/检查模式确认目标。禁止用无 version ID 的对象删除、无差别 `git clean`，或把测试清理入口指向非 `*-test` bucket/非测试数据库。
