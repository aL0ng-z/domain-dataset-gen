# 导出 orphan 对象版本清理

适用于 T11 导出在 payload/manifest 上传后、seal 发布前失败或取消所留下的未引用对象版本。工具不会删除任何被 `ExportArtifactSeal` 引用的坐标，也不会处理 `queued`/`processing` Export。

## 预检

1. `conda activate DatasetGen`。
2. 核对数据库和 MinIO 配置属于目标环境，并确认 outputs bucket 已启用 versioning。
3. 默认保留期为 24 小时；不得为了刚失败的任务临时缩短到 1 小时以下。
4. 先运行 dry-run：

```powershell
python scripts/export_orphan_cleanup.py --bucket outputs-test --prefix tests/<run_id>/
```

输出只列出符合内容寻址 key 结构、未被 seal 引用且不属于活跃 Export 的精确 `key + version_id`。

## 应用删除

逐项复核 dry-run 后，显式重复 bucket 名：

```powershell
python scripts/export_orphan_cleanup.py `
  --bucket outputs-test `
  --prefix tests/<run_id>/ `
  --older-than-hours 24 `
  --apply `
  --confirm-bucket outputs-test
```

`--apply` 缺少精确 `--confirm-bucket` 时工具会拒绝执行。删除始终绑定 version ID，包括 delete marker；禁止改成按 key 的无版本删除。

## 事后复核

- 再次运行同参数 dry-run，应显示 `candidates=0`。
- 对 completed Export 调用 `POST .../verify?deep=true`，确认 payload、manifest 对象和 canonical manifest hash 均通过。
- 若候选仍属于业务恢复窗口，停止清理并先完成 Task/Export 状态取证。
