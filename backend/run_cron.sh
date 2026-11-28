#!/bin/bash
LOG=/tmp/icechaser_cron.log
{
  echo "===== run $(date -u '+%Y-%m-%dT%H:%M:%SZ') ====="
  python3 /root/.openclaw/workspace/projects/icechaser/backend/generate_data_v3.py
  echo "exit=$?"
} >> "$LOG" 2>&1
