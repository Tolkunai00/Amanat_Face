#!/bin/sh
set -eu

[ "$#" -eq 2 ] || { echo "Usage: upload_backup_s3.sh BACKUP_PATH METADATA_PATH" >&2; exit 2; }
: "${OFFSITE_S3_URI:?OFFSITE_S3_URI is required, for example s3://bucket/amanat}"
command -v aws >/dev/null 2>&1 || {
  echo "aws CLI is not installed; use a dedicated uploader image or filesystem mode" >&2
  exit 3
}
aws s3 cp "$1" "$OFFSITE_S3_URI/$(basename "$1")" --only-show-errors
aws s3 cp "$2" "$OFFSITE_S3_URI/$(basename "$2")" --only-show-errors
