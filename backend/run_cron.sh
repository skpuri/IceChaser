#!/bin/bash
# Cron wrapper for generate_data_v3.py
# Logs stdout+stderr to /tmp/icechaser_cron.log
cd /root/.openclaw/workspace/projects/icechaser/backend || exit 1
{
  echo "===== RUN $(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="
  python3 generate_data_v3.py
  echo "===== EXIT: $? at $(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="
} >> /tmp/icechaser_cron.log 2>&1
