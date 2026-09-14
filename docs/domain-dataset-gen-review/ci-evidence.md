# 固定提交的 CI 证据摘录

仓库：aL0ng-z/domain-dataset-gen  
SHA：019f9ea70fb2325aad068e03cc7637372a9fe273  
读取日期：2026-09-14  
运行：34741941555，run number 2，event=push，attempt=1。

本文件仅摘录通过 GitHub 连接器实际读取的关键日志，并不是完整日志下载，也不是本次重新运行 CI。以下时间均为 UTC。

## 前端

原始作业：[frontend job 103682995323](https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555/job/103682995323)

环境：Node v22.23.2，npm 10.9.8。Install dependencies 失败；ESLint、TypeScript check、API contract drift + pattern check、Vitest、Next.js build 均 skipped。

```text
2026-09-13T06:06:51.0535038Z npm error code EUSAGE
2026-09-13T06:06:51.0536643Z npm error `npm ci` can only install packages when your package.json and package-lock.json or npm-shrinkwrap.json are in sync. Please update your lock file with `npm install` before continuing.
2026-09-13T06:06:51.0537887Z npm error Missing: @emnapi/core@2.0.0-alpha.3 from lock file
2026-09-13T06:06:51.0538534Z npm error Missing: @emnapi/runtime@2.0.0-alpha.3 from lock file
2026-09-13T06:06:51.0691117Z ##[error]Process completed with exit code 1.
2026-09-13T06:06:51.1914906Z ##[warning]No files were found with the provided path: apps/web/coverage/. No artifacts will be uploaded.
```

## 后端

原始作业：[backend job 103682995231](https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555/job/103682995231)

Initialize containers 失败；checkout、Setup MinIO test buckets、Python / uv 设置、安装、lint、OpenAPI 检查、迁移和 pytest 均 skipped。PostgreSQL / Redis 镜像拉取成功，MinIO 镜像拉取重试三次仍失败。

```text
2026-09-13T06:06:51.7569119Z ##[command]/usr/bin/docker pull minio/minio
2026-09-13T06:06:51.7689900Z Using default tag: latest
2026-09-13T06:06:51.8618909Z Error response from daemon: pull access denied for minio/minio, repository does not exist or may require 'docker login': denied: requested access to the resource is denied
2026-09-13T06:07:07.8354272Z ##[error]Docker pull failed with exit code 1
2026-09-13T06:07:08.0398439Z ##[warning]No files were found with the provided path: coverage-reports/. No artifacts will be uploaded.
```

## 解释边界

该记录证明的是此提交在这次运行环境中的交付阻断，而不是镜像全球永久不可用，也不是本仓库 Python 测试失败。没有实际执行的测试不得记为通过。CI 初始化中的 mc entrypoint 疑点也不是这次失败的直接原因，因为该步骤根本没有运行。
