#!/bin/sh
# Initialize MinIO buckets for the platform.
# This script is executed by the minio-init container after MinIO is healthy.

set -e

mc alias set local http://minio:9000 "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}"

mc mb --ignore-existing "local/${MINIO_BUCKET_DOCUMENTS}"
mc mb --ignore-existing "local/${MINIO_BUCKET_OUTPUTS}"

# T11：outputs bucket 必须启用 versioning（不可变导出依赖按 version_id 下载历史对象）。
mc version enable "local/${MINIO_BUCKET_OUTPUTS}"

echo "MinIO buckets initialized successfully"
